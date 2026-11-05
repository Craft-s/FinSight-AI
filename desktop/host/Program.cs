namespace FinSight.Desktop;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        using var singleInstance = new Mutex(true, @"Local\FinSightAI.Desktop", out var firstInstance);
        if (!firstInstance)
        {
            MessageBox.Show("FinSight AI 已经打开。请切换到现有窗口。", "FinSight AI",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        Application.Run(new DesktopWindow());
    }
}
