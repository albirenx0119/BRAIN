// Copyright 2026 Ren Y Sato
// SPDX-License-Identifier: Apache-2.0
// Native startup checks and installation prompts also work without Python.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    [DllImport("shell32.dll")] static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    static readonly string Home = AppDomain.CurrentDomain.BaseDirectory;
    static Form splash;
    static Label status;
    static string[] startupArgs;
    internal static string Argument(string value) { return Quote(value); }
    internal static void WriteLog(string value) { Log(value); }
    static readonly string Probe = @"import sys
if sys.version_info < (3,10):
 print('PYTHON_OLD'); sys.exit(0)
try:
 import tkinter, _tkinter
except Exception:
 print('TK_MISSING'); sys.exit(0)
from importlib.metadata import version
for dist, modules, low, high in [('Pillow',['PIL.Image','PIL.ImageTk'],(10,3),(13,0)),('numpy',['numpy'],(1,26),(3,0)),('opencv-python',['cv2'],(4,8),(5,0))]:
 try:
  import importlib
  for module in modules: importlib.import_module(module)
  installed=importlib.import_module('cv2').__version__ if dist=='opencv-python' else version(dist)
  v=tuple(int(x) for x in installed.split('.')[:2])
  if not low <= v < high: raise ValueError()
 except Exception: print(dist)
print('CHECK_COMPLETE')";

    static string Quote(string value)
    {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            result.Append('\\',ch == '"' ? slashes * 2 + 1 : slashes);
            result.Append(ch); slashes = 0;
        }
        result.Append('\\',slashes * 2); result.Append('"'); return result.ToString();
    }

    static int Run(string exe,string args,out string output)
    {
        var info = new ProcessStartInfo(exe,args);
        info.WorkingDirectory = Home; info.UseShellExecute = false; info.CreateNoWindow = true;
        info.RedirectStandardOutput = true; info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        var stdout = new StringBuilder(); var stderr = new StringBuilder();
        using (var process = new Process())
        {
            process.StartInfo = info;
            process.OutputDataReceived += delegate(object s,DataReceivedEventArgs e) { if (e.Data != null) stdout.AppendLine(e.Data); };
            process.ErrorDataReceived += delegate(object s,DataReceivedEventArgs e) { if (e.Data != null) stderr.AppendLine(e.Data); };
            process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine(); process.WaitForExit();
            output = stdout.ToString() + stderr.ToString(); return process.ExitCode;
        }
    }

    static IEnumerable<string> Candidates()
    {
        yield return Path.Combine(Home,".venv","Scripts","python.exe");
        yield return Path.Combine(Home,"runtime","python.exe");
        foreach (string part in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            if (part.IndexOf("WindowsApps",StringComparison.OrdinalIgnoreCase) < 0)
                yield return Path.Combine(part.Trim().Trim('"'),"python.exe");
        foreach (RegistryKey hive in new RegistryKey[] {Registry.CurrentUser,Registry.LocalMachine})
            using (RegistryKey core = hive.OpenSubKey(@"Software\Python\PythonCore"))
            {
                if (core == null) continue;
                foreach (string key in core.GetSubKeyNames())
                    using (RegistryKey install = core.OpenSubKey(key + @"\InstallPath"))
                    {
                        if (install == null) continue;
                        string path = install.GetValue("ExecutablePath") as string;
                        if (!String.IsNullOrEmpty(path)) yield return path;
                        path = install.GetValue("") as string;
                        if (!String.IsNullOrEmpty(path)) yield return Path.Combine(path,"python.exe");
                    }
            }
        string local = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","Python");
        if (Directory.Exists(local))
            foreach (string folder in Directory.GetDirectories(local)) yield return Path.Combine(folder,"python.exe");
    }

    static string FindPython(out string report,bool systemOnly=false)
    {
        report = ""; string partial = null, partialReport = "";
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string path in Candidates())
        {
            if (systemOnly && (IsAppEnvironment(path) || IsVirtualEnvironment(path))) continue;
            if (!seen.Add(path) || !File.Exists(path)) continue;
            try
            {
                string result;
                if (Run(path,"-X utf8 -c " + Quote(Probe),out result) != 0 || !result.Contains("CHECK_COMPLETE")) continue;
                if (path.StartsWith(Path.Combine(Home,".venv") + Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase) || result.Trim() == "CHECK_COMPLETE")
                { report = result; return path; }
                if (partial == null) { partial = path; partialReport = result; }
            }
            catch (Exception) { }
        }
        report = partialReport; return partial;
    }

    static bool IsAppEnvironment(string path)
    { return path!=null && path.StartsWith(Path.Combine(Home,".venv")+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase); }

    static bool IsVirtualEnvironment(string path)
    {
        try
        {
            string folder=Path.GetDirectoryName(Path.GetFullPath(path)),parent=Path.GetDirectoryName(folder);
            return File.Exists(Path.Combine(folder,"pyvenv.cfg")) || (parent!=null && File.Exists(Path.Combine(parent,"pyvenv.cfg")));
        }
        catch { return false; }
    }

    static string RuntimeMode()
    {
        try
        {
            var settings=new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(Path.Combine(Home,".local","runtime.json")));
            string mode=settings["mode"]; return mode=="system" || mode=="venv" ? mode : "";
        }
        catch { return ""; }
    }

    static void SaveRuntimeMode(string mode)
    {
        string folder=Path.Combine(Home,".local"); Directory.CreateDirectory(folder);
        File.WriteAllText(Path.Combine(folder,"runtime.json"),"{\"mode\":\""+mode+"\"}",Encoding.UTF8);
    }

    static string ChooseEnvironment(string report,bool missingPython)
    {
        return (string)splash.Invoke(new Func<string>(delegate {
            using (var dialog=new Form())
            {
                dialog.Text="BRAIN — 実行環境の準備"; dialog.StartPosition=FormStartPosition.CenterParent;
                dialog.FormBorderStyle=FormBorderStyle.FixedDialog; dialog.MinimizeBox=false;dialog.MaximizeBox=false;
                dialog.ClientSize=new Size(650,320); dialog.Icon=splash.Icon;
                var text=new Label(); text.SetBounds(18,16,615,225);
                text.Text="必要なものが不足しています。実行方法を選んでください。\n\n"+
                    (missingPython?"Python 3.10以降・Tkinter":"不足または対応外: "+report.Replace("CHECK_COMPLETE","").Trim())+
                    "\n\nPCのPython: このユーザーのPythonへライブラリを追加します。\n仮想環境: BRAIN専用の .venv を作り、そこで実行します。\n\nどちらもPC内で実行します。仮想環境にもPython本体が必要です。\nPythonがなければインストールします。初回ダウンロードにはネット接続が必要です。";
                dialog.Controls.Add(text);
                var local=new Button(); local.Text="PCのPythonにインストール";local.Name="setup-system";
                local.SetBounds(18,266,228,34);local.DialogResult=DialogResult.Yes;dialog.Controls.Add(local);
                var isolated=new Button();isolated.Text="仮想環境を作成";isolated.Name="setup-venv";
                isolated.SetBounds(258,266,220,34);isolated.DialogResult=DialogResult.No;dialog.Controls.Add(isolated);
                var cancel=new Button();cancel.Text="キャンセル";cancel.SetBounds(492,266,138,34);
                cancel.DialogResult=DialogResult.Cancel;dialog.Controls.Add(cancel);dialog.CancelButton=cancel;
                DialogResult result=dialog.ShowDialog(splash);
                return result==DialogResult.Yes?"system":result==DialogResult.No?"venv":null;
            }
        }));
    }

    static void Status(string text) { splash.Invoke((MethodInvoker)delegate { status.Text = text; }); }
    static bool Ask(string text)
    {
        return (bool)splash.Invoke(new Func<bool>(delegate {
            return MessageBox.Show(splash,text,"BRAIN — インストールの確認",MessageBoxButtons.YesNo,MessageBoxIcon.Question) == DialogResult.Yes;
        }));
    }
    static void Error(string text)
    {
        splash.Invoke((MethodInvoker)delegate { MessageBox.Show(splash,text,"BRAIN",MessageBoxButtons.OK,MessageBoxIcon.Error); });
    }
    static void Log(string text)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(Home,"logs"));
            File.AppendAllText(Path.Combine(Home,"logs","startup.log"),DateTime.Now.ToString("s") + "\n" + text + "\n",Encoding.UTF8);
        }
        catch (Exception) { }
    }

    static bool InstallPython(bool confirm=true)
    {
        if (confirm && !Ask("Python 3.10以降とTkinterが見つからないか、正常に使えません。\n\nPython 3.12（Tkinterを含む）をこのPCのユーザー用にインストールしますか？\nインターネット接続が必要です。")) return false;
        Status("Pythonをインストールしています…"); string output;
        try
        {
            int code = Run("winget.exe","install --exact --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity",out output);
            Log(output);
            if (code != 0) throw new Exception("インストーラーが正常終了しませんでした。");
            return true;
        }
        catch (Exception error)
        {
            Log(error.ToString());
            Error("Pythonのインストールを完了できませんでした。\n" + error.Message + "\n\nWindowsの「アプリ インストーラー」を更新するか、python.orgからPython（tcl/tkを含む）をインストールし、BRAIN.exeを再起動してください。\n詳細: logs\\startup.log");
            return false;
        }
    }

    static string InstallPackages(string python,string mode)
    {
        if (mode!="system" && mode!="venv") throw new Exception("実行環境の設定が不正です。");
        Status("必要なライブラリを準備しています…");
        string environment = Path.Combine(Home,".venv"), target = mode=="venv"?Path.Combine(Home,".venv","Scripts","python.exe"):python, output;
        if (mode=="venv" && !String.Equals(python,target,StringComparison.OrdinalIgnoreCase))
            if (Run(python,"-m venv " + Quote(environment),out output) != 0) { Log(output); throw new Exception("専用Python環境を作成できませんでした。"); }
        string user=mode=="system"?" --user":"";
        if (Run(target,"-m ensurepip --upgrade"+user,out output) != 0) { Log(output); throw new Exception("インストール機能（pip）を準備できませんでした。"); }
        if (Run(target,"-m pip install --disable-pip-version-check --upgrade"+user+" -r " + Quote(Path.Combine(Home,"function","requirements.txt")),out output) != 0)
        { Log(output); throw new Exception("インストールできませんでした。ネット接続と logs\\startup.log を確認してください。"); }
        Log(output);
        if (Run(target,"-X utf8 -c " + Quote(Probe),out output) != 0 || output.Trim() != "CHECK_COMPLETE")
        { Log(output); throw new Exception("インストール後の確認に失敗しました。logs\\startup.log を確認してください。"); }
        return target;
    }

    static void StartApplication()
    {
        string ready = null;
        try
        {
            if (startupArgs.Length==2 && startupArgs[0]=="--check-updates")
            {
                Updates.Check(splash,Home,Status,Int32.Parse(startupArgs[1]),true); return;
            }
            if (Array.IndexOf(startupArgs,"--skip-update-check")<0 && Updates.Check(splash,Home,Status,0,false)) return;
            string script = Path.Combine(Home,"function","BRAIN.py");
            if (!File.Exists(script) || !File.Exists(Path.Combine(Home,"function","requirements.txt")))
                throw new Exception("BRAIN.exeとfunctionフォルダを同じ場所に置いてください。ZIPは全体を展開してください。");
            Status("必要なものを確認しています…");
            string mode=RuntimeMode(),report,python=FindPython(out report,mode=="system");
            if (python==null || report.Trim()!="CHECK_COMPLETE" || (mode=="venv" && !IsAppEnvironment(python)))
            {
                mode=ChooseEnvironment(report,python==null); if (mode==null) return;
                if (mode=="system") python=FindPython(out report,true);
                if (python==null)
                {
                    if (!InstallPython(false)) return;
                    python=FindPython(out report,mode=="system");
                    if (python==null) throw new Exception("PythonとTkinterを確認できませんでした。PCを再起動してからBRAIN.exeを開いてください。");
                }
                if (mode=="venv" || report.Trim()!="CHECK_COMPLETE") python=InstallPackages(python,mode);
                SaveRuntimeMode(mode);
            }
            Status("BRAINを起動しています…");
            string windowed = Path.Combine(Path.GetDirectoryName(python),"pythonw.exe");
            ready = Path.Combine(Path.GetTempPath(),"BRAIN-ready-" + Guid.NewGuid().ToString("N"));
            var info = new ProcessStartInfo(File.Exists(windowed) ? windowed : python,"-X utf8 " + Quote(script) + " --app-start --startup-ready " + Quote(ready));
            info.WorkingDirectory = Home; info.UseShellExecute = false; info.CreateNoWindow = true;
            using (var process = Process.Start(info))
            {
                var watch = Stopwatch.StartNew();
                while (!File.Exists(ready))
                {
                    if (File.Exists(ready + ".error")) { Log(File.ReadAllText(ready + ".error")); throw new Exception("起動処理でエラーが発生しました。logs\\startup.log を確認してください。"); }
                    if (process.HasExited) throw new Exception("BRAINを起動できませんでした。logs\\startup.log を確認してください。");
                    if (watch.Elapsed.TotalSeconds > 120) throw new Exception("起動確認がタイムアウトしました。起動中のウィンドウを確認してください。");
                    Thread.Sleep(60);
                }
            }
            Thread.Sleep(350);
        }
        catch (Exception error) { Log(error.ToString()); Error(error.Message); }
        finally
        {
            if (ready != null)
            {
                if (File.Exists(ready)) File.Delete(ready);
                if (File.Exists(ready + ".error")) File.Delete(ready + ".error");
            }
            splash.BeginInvoke((MethodInvoker)delegate { splash.Close(); });
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        SetCurrentProcessExplicitAppUserModelID("BRAIN.Desktop");
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length>0 && args[0]=="--apply-update") { Updates.Apply(args); return; }
        startupArgs=args;
        splash = new Form(); splash.Text = "BRAIN — 起動中";
        splash.FormBorderStyle = FormBorderStyle.None; splash.StartPosition = FormStartPosition.CenterScreen;
        splash.ClientSize = new Size(360,410); splash.BackColor = Color.Black; splash.TopMost = true;
        splash.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var picture = new PictureBox();
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("BRAIN.Logo"))
        using (Image image = Image.FromStream(stream)) picture.Image = new Bitmap(image);
        picture.SizeMode = PictureBoxSizeMode.Zoom; picture.SetBounds(12,12,336,336); splash.Controls.Add(picture);
        status = new Label(); status.ForeColor = Color.White; status.BackColor = Color.Black;
        status.Font = new Font("Yu Gothic UI",10); status.TextAlign = ContentAlignment.MiddleCenter;
        status.SetBounds(10,350,340,50); status.Text = "起動準備中…"; splash.Controls.Add(status);
        splash.Shown += delegate { var worker = new Thread(StartApplication); worker.IsBackground = true; worker.Start(); };
        Application.Run(splash); picture.Image.Dispose();
    }
}
