using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace FinSight.Desktop;

internal sealed class DesktopWindow : Form
{
    private const string LocalOrigin = "https://finsight.local";
    private const string SessionMarker = "desktop-session";
    private const int MaxRequestBytes = 1024 * 1024;
    private const string PageHeaders = "Content-Type: text/html; charset=utf-8\r\n" +
        "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n" +
        "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; " +
        "frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\r\n";

    private static readonly HashSet<string> SourceHosts = new(StringComparer.OrdinalIgnoreCase)
    {
        "www.sec.gov", "data.sec.gov", "www.cninfo.com.cn", "static.cninfo.com.cn",
        "data.eastmoney.com", "datacenter.eastmoney.com", "datacenter-web.eastmoney.com",
        "np-anotice-stock.eastmoney.com", "pdf.dfcfw.com", "emweb.securities.eastmoney.com",
    };

    private readonly CancellationTokenSource lifetime = new();
    private readonly WebView2 view = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Dictionary<string, (string Path, string ContentType)> staticFiles = new(StringComparer.Ordinal);
    private readonly Label status = new()
    {
        Dock = DockStyle.Fill,
        TextAlign = ContentAlignment.MiddleCenter,
        ForeColor = Color.FromArgb(29, 44, 66),
        BackColor = Color.FromArgb(245, 247, 252),
        Padding = new Padding(32),
        AutoEllipsis = true,
    };
    private WslBridge? bridge;
    private byte[] indexPage = [];
    private Task startup = Task.CompletedTask;
    private Task? shutdown;
    private Task? bridgeStop;
    private bool closeComplete;

    public DesktopWindow()
    {
        Text = "FinSight AI";
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1240, 850);
        MinimumSize = new Size(600, 480);
        Controls.Add(view);
        Controls.Add(status);
        SetStatus("正在启动桌面工作台…");
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        startup = StartWorkspaceAsync();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        base.OnFormClosing(e);
        if (closeComplete)
            return;

        e.Cancel = true;
        if (shutdown is not null)
            return;

        lifetime.Cancel();
        SetStatus("正在关闭本地服务，请稍候…");
        shutdown = StopWorkspaceAsync();
    }

    private async Task StartWorkspaceAsync()
    {
        try
        {
            var bundle = Path.GetFullPath(Environment.GetEnvironmentVariable("FINSIGHT_DESKTOP_BUNDLE")
                ?? AppContext.BaseDirectory);
            var frontend = Path.Combine(bundle, "frontend", "dist");
            var index = Path.Combine(frontend, "index.html");
            if (!File.Exists(index) || !File.Exists(Path.Combine(bundle, "desktop", "runtime", "host.py")))
                throw new IOException("桌面程序文件不完整。请完整解压安装包后再次打开。");
            indexPage = await File.ReadAllBytesAsync(index, lifetime.Token);
            RegisterStaticFiles(frontend);

            var userDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "FinSightAI");
            var profile = Environment.GetEnvironmentVariable("FINSIGHT_DESKTOP_WEB_PROFILE")
                ?? Path.Combine(userDirectory, "WebView2");
            SetStatus("正在初始化桌面界面…");
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: Path.GetFullPath(profile));
            lifetime.Token.ThrowIfCancellationRequested();
            await view.EnsureCoreWebView2Async(environment);
            lifetime.Token.ThrowIfCancellationRequested();
            await ConfigureViewAsync();
            lifetime.Token.ThrowIfCancellationRequested();

            var progress = new Progress<string>(message =>
            {
                if (!lifetime.IsCancellationRequested)
                    SetStatus(message);
            });
            bridge = new WslBridge(ReadDistribution(userDirectory), bundle);
            bridge.StatusChanged += ((IProgress<string>)progress).Report;
            bridge.Disconnected += () => ((IProgress<string>)progress).Report("本地服务已断开，请关闭窗口后重新打开。");
            SetStatus("正在启动 WSL 与本地数据服务…");
            await bridge.StartAsync(lifetime.Token);
            lifetime.Token.ThrowIfCancellationRequested();
            SetStatus("正在加载工作台…");
            view.CoreWebView2.Navigate(LocalOrigin + "/");
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested)
        {
            // The close handler coordinates cleanup.
        }
        catch (Exception error)
        {
            if (!lifetime.IsCancellationRequested)
            {
                var message = error switch
                {
                    WebView2RuntimeNotFoundException => "未安装 Microsoft Edge WebView2 Runtime。请安装后重新打开。",
                    TimeoutException => "本地服务启动超时。请确认 WSL 初始化已完成后重试。",
                    JsonException => "桌面配置无法读取。请重新运行初始化。",
                    IOException => error.Message,
                    _ => "无法启动桌面工作台。请运行初始化并检查 WSL 与 WebView2。",
                };
                SetStatus(message);
                await StopBridgeAsync();
            }
        }
    }

    private void RegisterStaticFiles(string frontendDirectory)
    {
        var favicon = Path.Combine(frontendDirectory, "favicon.svg");
        if (File.Exists(favicon))
            staticFiles.Add("/favicon.svg", (favicon, "image/svg+xml"));
        var assets = Path.Combine(frontendDirectory, "assets");
        if (!Directory.Exists(assets))
            throw new IOException("桌面程序缺少界面资源。请完整解压安装包后再次打开。");
        foreach (var file in Directory.EnumerateFiles(assets))
        {
            var contentType = Path.GetExtension(file).ToLowerInvariant() switch
            {
                ".js" => "text/javascript; charset=utf-8",
                ".css" => "text/css; charset=utf-8",
                ".svg" => "image/svg+xml",
                ".woff2" => "font/woff2",
                _ => null,
            };
            if (contentType is not null)
                staticFiles.Add("/assets/" + Uri.EscapeDataString(Path.GetFileName(file)), (file, contentType));
        }
    }

    private async Task ConfigureViewAsync()
    {
        var browser = view.CoreWebView2;
        browser.Settings.AreHostObjectsAllowed = false;
        browser.Settings.IsWebMessageEnabled = false;
        browser.Settings.AreDevToolsEnabled = false;
        browser.Settings.AreDefaultContextMenusEnabled = false;
        browser.Settings.IsStatusBarEnabled = false;
        browser.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All, CoreWebView2WebResourceRequestSourceKinds.All);
        browser.WebResourceRequested += OnResourceRequested;
        browser.NavigationStarting += (sender, args) =>
        {
            if (IsLocal(args.Uri, out _))
                return;
            args.Cancel = true;
            OpenSource(args.Uri, args.IsUserInitiated);
        };
        browser.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            OpenSource(args.Uri, args.IsUserInitiated);
        };
        browser.FrameNavigationStarting += (_, args) => args.Cancel = true;
        browser.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
        browser.DownloadStarting += (_, args) =>
        {
            if (!IsLocalBlob(args.DownloadOperation.Uri) ||
                !string.Equals(Path.GetExtension(args.ResultFilePath), ".md", StringComparison.OrdinalIgnoreCase))
                args.Cancel = true;
        };
        browser.NavigationCompleted += (_, args) =>
        {
            if (lifetime.IsCancellationRequested)
                return;
            if (!args.IsSuccess)
            {
                if (args.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
                    SetStatus("无法加载桌面界面，请关闭窗口后重新打开。");
                return;
            }
            status.Visible = false;
            view.Visible = true;
            view.BringToFront();
        };
        await browser.AddScriptToExecuteOnDocumentCreatedAsync(
            "if (window.top === window && location.origin === 'https://finsight.local') " +
            "Object.defineProperty(window, 'finsightDesktop', { value: true });");
    }

    private async void OnResourceRequested(object? sender, CoreWebView2WebResourceRequestedEventArgs args)
    {
        var environment = view.CoreWebView2.Environment;
        var request = args.Request;
        if (!IsLocal(request.Uri, out var uri))
        {
            if (!IsLocalBlob(request.Uri))
                args.Response = Failure(environment, 403, "DESKTOP_ORIGIN_DENIED", "此资源不属于桌面应用。");
            return;
        }

        if (uri.AbsolutePath.StartsWith("/api/v1/", StringComparison.Ordinal) || uri.AbsolutePath == "/health")
        {
            if (request.Method is not ("GET" or "POST") || uri.PathAndQuery.Length > 4096)
            {
                args.Response = Failure(environment, 400, "DESKTOP_REQUEST_INVALID", "本地请求无效。");
                return;
            }

            var deferral = args.GetDeferral();
            try
            {
                if (bridge is null)
                    throw new IOException();
                var body = ReadRequestBody(request.Content);
                var authorized = request.Headers.Contains("X-Admin-Token") &&
                    request.Headers.GetHeader("X-Admin-Token") == SessionMarker;
                var contentType = request.Headers.Contains("Content-Type") ? request.Headers.GetHeader("Content-Type") : null;
                var result = await bridge.RequestAsync(request.Method, uri.PathAndQuery, contentType,
                    body, authorized, lifetime.Token);
                if (result.Status is < 200 or > 599 or (>= 300 and < 400))
                    throw new IOException();
                var headers = $"Content-Type: {SafeHeader(result.ContentType, "application/octet-stream")}\r\n" +
                    "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n";
                if (!string.IsNullOrEmpty(result.Disposition) && SafeHeader(result.Disposition, "") is { Length: > 0 } disposition)
                    headers += $"Content-Disposition: {disposition}\r\n";
                args.Response = Response(environment, result.Body, result.Status, headers);
            }
            catch (InvalidDataException)
            {
                args.Response = Failure(environment, 413, "DESKTOP_REQUEST_TOO_LARGE", "本地请求超过大小限制。");
            }
            catch (Exception error) when (error is IOException or OperationCanceledException or TimeoutException or InvalidOperationException or COMException)
            {
                if (!lifetime.IsCancellationRequested)
                    args.Response = Failure(environment, 503, "DESKTOP_SERVICE_UNAVAILABLE", "本地数据服务暂时不可用。");
            }
            finally
            {
                try
                {
                    deferral.Complete();
                }
                catch (COMException) when (lifetime.IsCancellationRequested)
                {
                    // The WebView may already have released a canceled request.
                }
            }
            return;
        }

        if (request.Method == "GET" && staticFiles.TryGetValue(uri.AbsolutePath, out var asset))
        {
            try
            {
                args.Response = Response(environment, File.ReadAllBytes(asset.Path), 200,
                    $"Content-Type: {asset.ContentType}\r\nX-Content-Type-Options: nosniff\r\n");
            }
            catch (IOException)
            {
                args.Response = Failure(environment, 404, "DESKTOP_ASSET_MISSING", "界面资源不可用。");
            }
        }
        // A reload on a Vue Router history route has no matching static file.
        else if (args.ResourceContext == CoreWebView2WebResourceContext.Document && request.Method == "GET" &&
            !uri.AbsolutePath.StartsWith("/assets/", StringComparison.Ordinal))
        {
            args.Response = Response(environment, indexPage, 200, PageHeaders);
        }
        else
        {
            args.Response = Failure(environment, 404, "DESKTOP_ASSET_MISSING", "界面资源不可用。");
        }
    }

    private void SetStatus(string message)
    {
        view.Visible = false;
        status.Text = $"FinSight AI\r\n\r\n{message}";
        status.Visible = true;
        status.BringToFront();
    }

    private async Task StopWorkspaceAsync()
    {
        await Task.Yield();
        try
        {
            await startup;
            await StopBridgeAsync();
        }
        finally
        {
            closeComplete = true;
            Close();
        }
    }

    private Task StopBridgeAsync() => bridgeStop ??= StopBridgeCoreAsync();

    private async Task StopBridgeCoreAsync()
    {
        try
        {
            if (bridge is not null)
                await bridge.StopAsync();
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or TimeoutException or InvalidOperationException or Win32Exception)
        {
            // A stopped or disconnected subprocess cannot prevent the window closing.
        }
    }

    private static string ReadDistribution(string userDirectory)
    {
        var configured = Environment.GetEnvironmentVariable("FINSIGHT_DESKTOP_DISTRO");
        var configPath = Path.Combine(userDirectory, "desktop.local.json");
        if (string.IsNullOrWhiteSpace(configured) && File.Exists(configPath))
        {
            using var config = JsonDocument.Parse(File.ReadAllBytes(configPath));
            if (config.RootElement.TryGetProperty("distribution", out var property))
                configured = property.GetString();
        }
        return string.IsNullOrWhiteSpace(configured) ? "Ubuntu-24.04" : configured.Trim();
    }

    private static bool IsHttps(Uri uri) => uri.Scheme == Uri.UriSchemeHttps && uri.Port == 443 && uri.UserInfo.Length == 0;

    private static bool IsLocal(string value, out Uri uri)
    {
        if (Uri.TryCreate(value, UriKind.Absolute, out var parsed) && IsHttps(parsed) &&
            parsed.IdnHost.Equals("finsight.local", StringComparison.OrdinalIgnoreCase))
        {
            uri = parsed;
            return true;
        }
        uri = null!;
        return false;
    }

    private static bool IsLocalBlob(string value) => value.StartsWith("blob:" + LocalOrigin + "/", StringComparison.Ordinal);

    private static void OpenSource(string value, bool userInitiated)
    {
        if (!userInitiated || !Uri.TryCreate(value, UriKind.Absolute, out var uri) || !IsHttps(uri) || !SourceHosts.Contains(uri.IdnHost))
            return;
        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true })?.Dispose();
        }
        catch (Win32Exception)
        {
            MessageBox.Show("无法打开默认浏览器。", "FinSight AI", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
    }

    private static byte[] ReadRequestBody(Stream? input)
    {
        if (input is null)
            return [];
        using var output = new MemoryStream();
        var buffer = new byte[8192];
        int count;
        while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
        {
            if (output.Length + count > MaxRequestBytes)
                throw new InvalidDataException();
            output.Write(buffer, 0, count);
        }
        return output.ToArray();
    }

    private static string SafeHeader(string value, string fallback) =>
        value.Length <= 4096 && !value.Any(char.IsControl) ? value : fallback;

    private static CoreWebView2WebResourceResponse Failure(CoreWebView2Environment environment, int statusCode,
        string code, string message) => Response(environment,
        JsonSerializer.SerializeToUtf8Bytes(new { error = new { code, message } }), statusCode,
        "Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n");

    private static CoreWebView2WebResourceResponse Response(CoreWebView2Environment environment, byte[] body,
        int statusCode, string headers) => environment.CreateWebResourceResponse(new MemoryStream(body, writable: false),
        statusCode, statusCode < 300 ? "OK" : "Request failed", headers);
}
