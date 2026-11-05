using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace FinSight.Desktop;

internal sealed record BridgeResponse(int Status, string ContentType, string? Disposition, byte[] Body);

internal sealed class WslBridge(string distribution, string bundleDirectory)
{
    private const int RequestTimeoutSeconds = 170;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<BridgeResponse>> pending = new();
    private readonly SemaphoreSlim writeLock = new(1);
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Process? process;
    private Task? readTask;
    private long requestId;
    private volatile bool stopping;

    public event Action<string>? StatusChanged;
    public event Action? Disconnected;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        var mountedBundle = await MountPathAsync(cancellationToken).ConfigureAwait(false);
        cancellationToken.ThrowIfCancellationRequested();
        var startInfo = WslStartInfo("--user", "finsight-desktop", "--exec", "python3", "-I",
            $"{mountedBundle}/desktop/runtime/host.py", mountedBundle);
        process = Process.Start(startInfo) ?? throw new InvalidOperationException("无法启动 WSL 服务。");
        _ = process.StandardError.BaseStream.CopyToAsync(Stream.Null);
        readTask = ReadResponsesAsync(process.StandardOutput);
        await ready.Task.WaitAsync(TimeSpan.FromMinutes(4), cancellationToken).ConfigureAwait(false);
    }

    public async Task<BridgeResponse> RequestAsync(string method, string path, string? contentType,
        byte[] body, bool authorized, CancellationToken cancellationToken)
    {
        if (!ready.Task.IsCompletedSuccessfully || stopping)
            throw new IOException("本地服务尚未就绪。");

        var id = Interlocked.Increment(ref requestId);
        var reply = new TaskCompletionSource<BridgeResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = reply;
        try
        {
            var request = new { id, method, path, contentType, body = Convert.ToBase64String(body), authorized };
            await writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                if (stopping || process is null || process.HasExited)
                    throw new IOException("本地服务已退出。");
                await WriteLineAsync(process.StandardInput, request, cancellationToken).ConfigureAwait(false);
            }
            finally
            {
                writeLock.Release();
            }
            return await reply.Task.WaitAsync(TimeSpan.FromSeconds(RequestTimeoutSeconds), cancellationToken)
                .ConfigureAwait(false);
        }
        finally
        {
            pending.TryRemove(id, out _);
        }
    }

    public async Task StopAsync()
    {
        stopping = true;
        var child = process;
        if (child is null)
            return;

        try
        {
            if (!child.HasExited)
            {
                using var writeTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                try
                {
                    await writeLock.WaitAsync(writeTimeout.Token).ConfigureAwait(false);
                    try
                    {
                        await WriteLineAsync(child.StandardInput, new { type = "shutdown" }, writeTimeout.Token)
                            .ConfigureAwait(false);
                    }
                    finally
                    {
                        writeLock.Release();
                    }
                }
                catch (Exception error) when (error is IOException or OperationCanceledException or InvalidOperationException)
                {
                    // Closing the pipe also signals shutdown if the control message did not arrive.
                }
                try
                {
                    child.StandardInput.Close();
                }
                catch (Exception error) when (error is IOException or InvalidOperationException)
                {
                    // The child can close its end of the pipe first.
                }
                using var stopTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
                try
                {
                    await child.WaitForExitAsync(stopTimeout.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    KillProcess(child);
                    using var killTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                    await child.WaitForExitAsync(killTimeout.Token).ConfigureAwait(false);
                }
            }
            if (readTask is not null)
                await readTask.WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
        }
        finally
        {
            child.Dispose();
        }
    }

    private async Task ReadResponsesAsync(StreamReader output)
    {
        try
        {
            while (await output.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                using var document = JsonDocument.Parse(line);
                var item = document.RootElement;
                if (item.TryGetProperty("type", out var type))
                {
                    var message = item.TryGetProperty("message", out var field) ? field.GetString() : null;
                    switch (type.GetString())
                    {
                        case "ready":
                            ready.TrySetResult();
                            break;
                        case "status" when message is not null:
                            StatusChanged?.Invoke(message);
                            break;
                        case "error":
                            ready.TrySetException(new IOException(message ?? "本地服务启动失败。"));
                            break;
                    }
                }
                else if (item.TryGetProperty("id", out var request) && pending.TryGetValue(request.GetInt64(), out var reply))
                {
                    var response = new BridgeResponse(item.GetProperty("status").GetInt32(),
                        item.GetProperty("contentType").GetString() ?? "application/octet-stream",
                        item.TryGetProperty("disposition", out var disposition) ? disposition.GetString() : null,
                        item.GetProperty("body").GetBytesFromBase64());
                    reply.TrySetResult(response);
                }
            }
        }
        catch (Exception error) when (error is IOException or JsonException or InvalidOperationException or KeyNotFoundException or FormatException)
        {
            // A lost or invalid transport must fail pending requests without retaining raw output.
        }
        finally
        {
            var error = new IOException("本地服务已断开。请完成初始化后重新打开 FinSight AI。");
            ready.TrySetException(error);
            foreach (var reply in pending.Values)
                reply.TrySetException(error);
            if (!stopping && ready.Task.IsCompletedSuccessfully)
                Disconnected?.Invoke();
        }
    }

    private async Task<string> MountPathAsync(CancellationToken cancellationToken)
    {
        var startInfo = WslStartInfo("--exec", "wslpath", "-a", "-u", bundleDirectory);
        using var conversion = Process.Start(startInfo) ?? throw new InvalidOperationException("无法定位 WSL 文件路径。");
        var output = conversion.StandardOutput.ReadToEndAsync(cancellationToken);
        _ = conversion.StandardError.BaseStream.CopyToAsync(Stream.Null);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            await conversion.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            var path = (await output.ConfigureAwait(false)).TrimEnd('\r', '\n');
            if (conversion.ExitCode != 0 || !path.StartsWith('/') || path.Contains('\n') || path.Contains('\0'))
                throw new IOException("无法读取 WSL 中的程序目录。请检查 Linux 发行版并运行初始化。");
            return path.TrimEnd('/');
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            KillProcess(conversion);
            throw new TimeoutException("等待 WSL 启动超时。请检查 Linux 发行版状态。");
        }
        catch (OperationCanceledException)
        {
            KillProcess(conversion);
            throw;
        }
    }

    private ProcessStartInfo WslStartInfo(params string[] arguments)
    {
        var command = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wsl.exe");
        if (!File.Exists(command))
            throw new IOException("找不到 WSL。请安装 WSL 2 并运行桌面初始化。");

        var startInfo = new ProcessStartInfo(command)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("--distribution");
        startInfo.ArgumentList.Add(distribution);
        foreach (var argument in arguments)
            startInfo.ArgumentList.Add(argument);
        return startInfo;
    }

    private static async Task WriteLineAsync<T>(StreamWriter writer, T item, CancellationToken cancellationToken)
    {
        var serialized = JsonSerializer.Serialize(item, JsonOptions);
        await writer.WriteLineAsync(serialized.AsMemory(), cancellationToken).ConfigureAwait(false);
        await writer.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private static void KillProcess(Process child)
    {
        try
        {
            if (!child.HasExited)
                child.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException)
        {
            // The child may have exited between the check and the kill request.
        }
    }
}
