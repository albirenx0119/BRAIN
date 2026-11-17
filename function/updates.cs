// Copyright 2026 Ren Y Sato
// SPDX-License-Identifier: Apache-2.0
// Updates are fetched only from the application's fixed public GitHub repository.
using System;
using System.Collections.Generic;
using System.Collections;
using System.Drawing;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class Updates
{
    internal const string Repository = "https://github.com/albirenx0119/BRAIN";
    const string ManifestUrl = "https://raw.githubusercontent.com/albirenx0119/BRAIN/main/version.json";
    const long MaxFileBytes = 100 * 1024 * 1024;
    const long MaxExpanded = 300 * 1024 * 1024;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    delegate bool EnumCallback(IntPtr hwnd,IntPtr value);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumCallback callback,IntPtr value);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd,StringBuilder text,int size);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd,uint message,IntPtr wparam,IntPtr lparam);

    internal sealed class Manifest
    {
        internal string Number,Tag;
        internal Version Version;
        internal Dictionary<string,string> Files = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        internal List<ReleaseNote> Notes = new List<ReleaseNote>();
    }

    internal sealed class ReleaseNote
    {
        internal string Number;
        internal Version Version;
        internal List<string> Changes = new List<string>();
    }

    internal static bool AllowedPath(string path)
    {
        if (path == "BRAIN.exe" || path == "README.md" || path == "LICENSE" || path == "NOTICE") return true;
        if (!path.StartsWith("function/",StringComparison.Ordinal) || !Regex.IsMatch(path,@"^[A-Za-z0-9_./-]+$")) return false;
        foreach (string part in path.Split('/'))
        {
            if (part.Length==0 || part.StartsWith(".") || part.EndsWith(".") ||
                Regex.IsMatch(part,@"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)",RegexOptions.IgnoreCase)) return false;
        }
        return Regex.IsMatch(path,@"\.(py|cs|cmd|ico|jpg|png|txt)$",RegexOptions.IgnoreCase);
    }

    internal static Manifest Parse(string text)
    {
        if (text.Length > 65536) throw new Exception("更新情報が大きすぎます。");
        var data = Json.Deserialize<Dictionary<string,object>>(text);
        var result = new Manifest();
        result.Number = (string)data["version"]; result.Tag = (string)data["release_tag"];
        if (!Regex.IsMatch(result.Number,@"^\d+\.\d+\.\d+$") || result.Tag != "v"+result.Number ||
            (string)data["repository"] != Repository) throw new Exception("更新情報の形式が正しくありません。");
        result.Version = new Version(result.Number);
        object rawNotes;
        if (data.TryGetValue("release_notes",out rawNotes) && rawNotes is IEnumerable && !(rawNotes is string))
        {
            int count=0;
            foreach (object entry in (IEnumerable)rawNotes)
            {
                if (++count>50) break;
                var note=entry as Dictionary<string,object>;
                object number,changes;
                if (note==null || !note.TryGetValue("version",out number) || !(number is string) ||
                    !Regex.IsMatch((string)number,@"^\d+\.\d+\.\d+$") || !note.TryGetValue("changes",out changes) ||
                    !(changes is IEnumerable) || changes is string) continue;
                Version version;
                if (!Version.TryParse((string)number,out version)) continue;
                var release=new ReleaseNote {Number=(string)number,Version=version};
                foreach (object change in (IEnumerable)changes)
                {
                    string line=change as string;
                    if (!String.IsNullOrWhiteSpace(line)) release.Changes.Add(line.Trim().Substring(0,Math.Min(line.Trim().Length,1500)));
                    if (release.Changes.Count>=100) break;
                }
                if (release.Changes.Count>0) result.Notes.Add(release);
            }
        }
        var files = (Dictionary<string,object>)data["files"];
        if (files.Count>200 || files.Count<5) throw new Exception("更新ファイル一覧が正しくありません。");
        foreach (var item in files)
        {
            string hash = item.Value as string;
            if (!AllowedPath(item.Key) || hash==null || !Regex.IsMatch(hash,@"^[0-9a-f]{64}$",RegexOptions.IgnoreCase))
                throw new Exception("不正な更新ファイルです: "+item.Key);
            result.Files.Add(item.Key,hash);
        }
        foreach (string required in new string[] {"BRAIN.exe","README.md","function/BRAIN.py","function/requirements.txt","function/BRAIN.ico","function/assets/logo.png","LICENSE","NOTICE"})
            if (!result.Files.ContainsKey(required)) throw new Exception("必要な更新ファイルがありません: "+required);
        return result;
    }

    static HttpWebResponse Request(string url,int timeout)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        var request = (HttpWebRequest)WebRequest.Create(url);
        request.UserAgent = "BRAIN-updater"; request.Timeout = timeout; request.ReadWriteTimeout = timeout;
        request.AutomaticDecompression = DecompressionMethods.GZip|DecompressionMethods.Deflate;
        return (HttpWebResponse)request.GetResponse();
    }

    static string FetchManifest()
    {
        using (var response = Request(ManifestUrl+"?check="+DateTime.UtcNow.Ticks,4000))
        using (var stream = response.GetResponseStream())
        using (var buffer = new MemoryStream())
        {
            byte[] chunk = new byte[8192]; int count;
            while ((count=stream.Read(chunk,0,chunk.Length))>0)
            {
                if (buffer.Length+count>65536) throw new Exception("更新情報が大きすぎます。");
                buffer.Write(chunk,0,count);
            }
            return Encoding.UTF8.GetString(buffer.ToArray()).TrimStart('\ufeff');
        }
    }

    static string Hash(string path)
    {
        using (var input = File.OpenRead(path))
        using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-","").ToLowerInvariant();
    }

    static string Under(string root,string relative)
    {
        string full = Path.GetFullPath(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)));
        string prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)) throw new Exception("不正な保存先です。");
        // Refuse junctions and symlinks, so an application directory cannot redirect writes elsewhere.
        for (string part = full; part.Length>=prefix.Length; part=Path.GetDirectoryName(part))
            if ((Directory.Exists(part)||File.Exists(part)) && (File.GetAttributes(part)&FileAttributes.ReparsePoint)!=0)
                throw new Exception("更新先にリンクがあります: "+part);
        return full;
    }

    static void Verify(string payload,Manifest manifest)
    {
        foreach (var item in manifest.Files)
        {
            string path = Under(payload,item.Key);
            if (!File.Exists(path) || !String.Equals(Hash(path),item.Value,StringComparison.OrdinalIgnoreCase))
                throw new Exception("更新ファイルの検証に失敗しました: "+item.Key);
        }
    }

    static void CopyBounded(Stream input,Stream output,long expected)
    {
        byte[] buffer=new byte[65536]; int count; long total=0;
        while ((count=input.Read(buffer,0,buffer.Length))>0)
        {
            total+=count;
            if (total>expected || total>MaxExpanded) throw new Exception("展開サイズが一致しません。");
            output.Write(buffer,0,count);
        }
        if (total!=expected) throw new Exception("更新ファイルが不完全です。");
    }

    internal static void Extract(string archive,string payload,Manifest expected)
    {
        Directory.CreateDirectory(payload);
        using (var zip = ZipFile.OpenRead(archive))
        {
            if (zip.Entries.Count>1000) throw new Exception("更新ファイルが多すぎます。");
            var entries = new Dictionary<string,ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
            long expanded = 0; string prefix = null;
            foreach (var entry in zip.Entries)
            {
                string name = entry.FullName;
                if (name.Contains("\\") || name.StartsWith("/") || name.Contains(":")) throw new Exception("不正なZIPパスです。");
                string[] pieces = name.Split('/');
                foreach (string piece in pieces) if (piece==".." || piece==".") throw new Exception("不正なZIPパスです。");
                if (pieces.Length<2) throw new Exception("更新ZIPの構造が正しくありません。");
                if (prefix==null) prefix=pieces[0];
                if (prefix!=pieces[0]) throw new Exception("更新ZIPの構造が正しくありません。");
                expanded += entry.Length;
                if (expanded>MaxExpanded) throw new Exception("展開サイズが大きすぎます。");
                if (name.EndsWith("/")) continue;
                string relative=name.Substring(prefix.Length+1);
                if (entries.ContainsKey(relative)) throw new Exception("更新ファイルが重複しています。");
                entries.Add(relative,entry);
            }
            ZipArchiveEntry version;
            if (!entries.TryGetValue("version.json",out version) || version.Length>65536) throw new Exception("更新情報がありません。");
            Manifest actual;
            using (var reader = new StreamReader(version.Open(),Encoding.UTF8)) actual = Parse(reader.ReadToEnd());
            if (actual.Number!=expected.Number || actual.Files.Count!=expected.Files.Count) throw new Exception("公開情報と更新パッケージが一致しません。");
            foreach (var item in expected.Files)
                if (!actual.Files.ContainsKey(item.Key) || !String.Equals(actual.Files[item.Key],item.Value,StringComparison.OrdinalIgnoreCase))
                    throw new Exception("公開情報と更新パッケージが一致しません。");
            foreach (string relative in actual.Files.Keys)
            {
                ZipArchiveEntry entry;
                if (!entries.TryGetValue(relative,out entry)) throw new Exception("更新ファイルがありません: "+relative);
                string destination=Under(payload,relative); Directory.CreateDirectory(Path.GetDirectoryName(destination));
                using (var input=entry.Open()) using (var output=File.Create(destination)) CopyBounded(input,output,entry.Length);
            }
            using (var input=version.Open()) using (var output=File.Create(Under(payload,"version.json"))) CopyBounded(input,output,version.Length);
            Verify(payload,actual);
        }
    }

    static bool Matches(string path,string expected)
    {
        return File.Exists(path) && String.Equals(Hash(path),expected,StringComparison.OrdinalIgnoreCase);
    }

    internal static List<string> ChangedFiles(string home,Manifest manifest)
    {
        var result=new List<string>();
        foreach (var item in manifest.Files)
            if (!Matches(Under(home,item.Key),item.Value)) result.Add(item.Key);
        result.Sort(StringComparer.Ordinal); return result;
    }

    static void CheckManifest(Manifest actual,Manifest expected)
    {
        if (actual.Number!=expected.Number || actual.Tag!=expected.Tag || actual.Files.Count!=expected.Files.Count)
            throw new Exception("公開情報と更新先のファイル一覧が一致しません。");
        foreach (var item in expected.Files)
            if (!actual.Files.ContainsKey(item.Key) || !String.Equals(actual.Files[item.Key],item.Value,StringComparison.OrdinalIgnoreCase))
                throw new Exception("公開情報と更新先のファイル一覧が一致しません。");
    }

    static void VerifyPrepared(string home,string payload,Manifest manifest)
    {
        foreach (var item in manifest.Files)
        {
            string staged=Under(payload,item.Key);
            string path=File.Exists(staged) ? staged : Under(home,item.Key);
            if (!Matches(path,item.Value)) throw new Exception("更新ファイルの検証に失敗しました: "+item.Key);
        }
    }

    // The fetch callback writes one fixed-release file; tests substitute local files.
    internal static string PrepareFiles(string home,Manifest remote,Action<string,string> fetch,Action<string> status)
    {
        string updates=Under(home,".updates"); Directory.CreateDirectory(updates);
        string stage=Under(updates,"stage-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(stage);
        try
        {
            string payload=Path.Combine(stage,"payload"); Directory.CreateDirectory(payload);
            string metadata=Under(payload,"version.json"); fetch("version.json",metadata);
            if (new FileInfo(metadata).Length>65536) throw new Exception("更新情報が大きすぎます。");
            Manifest pinned=Parse(File.ReadAllText(metadata,Encoding.UTF8)); CheckManifest(pinned,remote);
            List<string> changed=ChangedFiles(home,pinned);
            status("変更・追加ファイル: "+changed.Count+" / "+pinned.Files.Count+" 件");
            long total=0;
            for (int i=0;i<changed.Count;i++)
            {
                string relative=changed[i],destination=Under(payload,relative);
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                status("変更ファイルをダウンロード中… "+(i+1)+" / "+changed.Count+" · "+relative);
                fetch(relative,destination);
                long length=new FileInfo(destination).Length; total+=length;
                if (length>MaxFileBytes || total>MaxExpanded) throw new Exception("更新ファイルが大きすぎます。");
                if (!Matches(destination,pinned.Files[relative])) throw new Exception("更新ファイルの検証に失敗しました: "+relative);
            }
            // Unchanged files are verified in place, rather than copied or downloaded.
            VerifyPrepared(home,payload,pinned); return stage;
        }
        catch { DeleteStage(home,stage); throw; }
    }

    static void DownloadFile(string url,string destination,long maximum)
    {
        using (var response=Request(url,15000))
        using (var input=response.GetResponseStream())
        using (var output=File.Create(destination))
        {
            if (response.ContentLength>maximum) throw new Exception("更新ファイルが大きすぎます。");
            byte[] buffer=new byte[65536]; int count;
            while ((count=input.Read(buffer,0,buffer.Length))>0)
            {
                if (output.Length+count>maximum) throw new Exception("更新ファイルが大きすぎます。");
                output.Write(buffer,0,count);
            }
        }
    }

    static string Download(string home,Manifest remote,Action<string> status)
    {
        return PrepareFiles(home,remote,delegate(string relative,string destination) {
            string url="https://raw.githubusercontent.com/albirenx0119/BRAIN/"+remote.Tag+"/"+relative;
            DownloadFile(url,destination,relative=="version.json" ? 65536 : MaxFileBytes);
        },status);
    }

    static void DeleteStage(string home,string stage)
    {
        string parent=Path.GetFullPath(Path.Combine(home,".updates"));
        if (Path.GetDirectoryName(Path.GetFullPath(stage))!=parent || !Path.GetFileName(stage).StartsWith("stage-"))
            throw new Exception("不正な一時フォルダです。");
        // Under also rejects directory links before any recursive removal.
        Under(parent,Path.GetFileName(stage));
        if (Directory.Exists(stage)) Directory.Delete(stage,true);
    }

    static void Show(Form parent,string message,MessageBoxIcon icon)
    { parent.Invoke((MethodInvoker)delegate { MessageBox.Show(parent,message,"BRAIN — アップデート",MessageBoxButtons.OK,icon); }); }

    internal static string ChangeSummary(Manifest local,Manifest remote)
    {
        var notes=new List<ReleaseNote>(remote.Notes);
        notes.Sort(delegate(ReleaseNote a,ReleaseNote b) { return b.Version.CompareTo(a.Version); });
        var text=new StringBuilder();
        foreach (var note in notes)
        {
            if (note.Version<=local.Version || note.Version>remote.Version) continue;
            text.AppendLine("v"+note.Number);
            foreach (string change in note.Changes) text.AppendLine("・"+change);
            text.AppendLine();
        }
        return text.Length>0?text.ToString().TrimEnd():"この更新の変更内容は公開されていません。";
    }

    internal static Form CreateUpdateDialog(Manifest local,Manifest remote,int runningPid)
    {
        var dialog=new Form();
        dialog.Text="BRAIN — アップデート"; dialog.StartPosition=FormStartPosition.CenterParent;
        dialog.ClientSize=new Size(680,510); dialog.MinimumSize=new Size(560,430);
        dialog.MinimizeBox=false; dialog.MaximizeBox=false; dialog.ShowInTaskbar=false;
        dialog.Font=new Font("Yu Gothic UI",10);
        var layout=new TableLayoutPanel {Dock=DockStyle.Fill,Padding=new Padding(18),ColumnCount=1,RowCount=4};
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        dialog.Controls.Add(layout);
        var heading=new Label {AutoSize=true,Dock=DockStyle.Fill,Margin=new Padding(0,0,0,12),
            Text="新しいバージョンがあります。\n現在: "+local.Number+"  →  新しいバージョン: "+remote.Number+"\n\n変更・追加された機能"};
        layout.Controls.Add(heading,0,0);
        var notes=new TextBox {Name="ReleaseNotes",Multiline=true,ReadOnly=true,WordWrap=true,
            ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill,Text=ChangeSummary(local,remote)};
        layout.Controls.Add(notes,0,1);
        layout.Controls.Add(new Label {AutoSize=true,Dock=DockStyle.Fill,Margin=new Padding(0,12,0,12),
            MaximumSize=new Size(620,0),Text="更新後にBRAINを再起動します。"+
            (runningPid>0?"\n未保存の解析結果は失われます。先に保存する場合は「今はしない」を選んでください。":"")+
            "\nアップデートしますか？"},0,2);
        var buttons=new FlowLayoutPanel {AutoSize=true,Dock=DockStyle.Fill,FlowDirection=FlowDirection.RightToLeft,WrapContents=false};
        var later=new Button {Text="今はしない",AutoSize=true,Padding=new Padding(8,4,8,4),DialogResult=DialogResult.No};
        var update=new Button {Text="アップデートする",AutoSize=true,Padding=new Padding(8,4,8,4),DialogResult=DialogResult.Yes};
        buttons.Controls.Add(later); buttons.Controls.Add(update); layout.Controls.Add(buttons,0,3);
        dialog.CancelButton=later; dialog.ActiveControl=later;
        return dialog;
    }

    internal static bool Check(Form parent,string home,Action<string> status,int runningPid,bool manual)
    {
        try
        {
            string localFile=Path.Combine(home,"version.json");
            if (!File.Exists(localFile))
            {
                if (manual) Show(parent,"現在のバージョン情報がありません。公式ZIPを展開し直してください。",MessageBoxIcon.Warning);
                return false;
            }
            status("GitHubのバージョンを確認しています…");
            Manifest local=Parse(File.ReadAllText(localFile,Encoding.UTF8));
            Manifest remote=Parse(FetchManifest());
            if (remote.Version<=local.Version)
            {
                if (manual) Show(parent,"現在のバージョン "+local.Number+" は最新です。",MessageBoxIcon.Information);
                return false;
            }
            bool yes=(bool)parent.Invoke(new Func<bool>(delegate {
                using (var dialog=CreateUpdateDialog(local,remote,runningPid)) return dialog.ShowDialog(parent)==DialogResult.Yes;
            }));
            if (!yes) return false;
            string stage=Download(home,remote,status);
            string helperFolder=Path.Combine(Path.GetTempPath(),"BRAIN-updater-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(helperFolder);
            string helper=Path.Combine(helperFolder,"BRAIN.exe"); File.Copy(Application.ExecutablePath,helper);
            var info=new ProcessStartInfo(helper,"--apply-update "+Launcher.Argument(home)+" "+Launcher.Argument(stage)+" "+Process.GetCurrentProcess().Id+" "+runningPid);
            info.UseShellExecute=false; info.CreateNoWindow=true; info.WorkingDirectory=home;
            Process.Start(info); return true;
        }
        catch (Exception error)
        {
            Launcher.WriteLog(error.ToString());
            if (manual) Show(parent,"更新を確認・準備できませんでした。\n"+error.Message+"\n\n現在のバージョンを引き続き使えます。",MessageBoxIcon.Warning);
            return false;
        }
    }

    static void WaitForExit(int pid)
    {
        if (pid<=0) return;
        try { using (var process=Process.GetProcessById(pid)) if (!process.WaitForExit(120000)) throw new Exception("BRAINの終了を確認できません。現在のバージョンを保持しました。"); }
        catch (ArgumentException) { }
    }

    static void CloseApplication(int pid)
    {
        if (pid<=0) return;
        EnumWindows(delegate(IntPtr hwnd,IntPtr value) {
            uint windowPid; GetWindowThreadProcessId(hwnd,out windowPid);
            var title=new StringBuilder(256); GetWindowText(hwnd,title,title.Capacity);
            if (windowPid==(uint)pid && title.ToString()=="BRAIN") PostMessage(hwnd,0x10,IntPtr.Zero,IntPtr.Zero);
            return true;
        },IntPtr.Zero);
    }

    internal static void Install(string home,string stage,Action<string> beforeFile)
    {
        home=Path.GetFullPath(home); stage=Path.GetFullPath(stage);
        if (Path.GetDirectoryName(stage)!=Path.Combine(home,".updates") || !Path.GetFileName(stage).StartsWith("stage-")) throw new Exception("不正な更新フォルダです。");
        Under(home,".updates/"+Path.GetFileName(stage));
        string payload=Path.Combine(stage,"payload"), backup=Path.Combine(stage,"backup");
        Manifest manifest=Parse(File.ReadAllText(Under(payload,"version.json"),Encoding.UTF8)); VerifyPrepared(home,payload,manifest);
        string localVersion=Path.Combine(home,"version.json");
        if (File.Exists(localVersion) && Parse(File.ReadAllText(localVersion,Encoding.UTF8)).Version>=manifest.Version) return;
        var changed=new List<string>(); var existed=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var files=new List<string>();
        foreach (var item in manifest.Files)
            if (File.Exists(Under(payload,item.Key)) && !Matches(Under(home,item.Key),item.Value)) files.Add(item.Key);
        files.Sort(StringComparer.Ordinal); files.Add("version.json");
        try
        {
            foreach (string relative in files)
            {
                if (relative=="version.json") Verify(home,manifest);
                string destination=Under(home,relative), old=Under(backup,relative);
                if (File.Exists(destination))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(old)); File.Copy(destination,old,true); existed.Add(relative);
                }
                beforeFile(relative);
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                changed.Add(relative);
                Replace(Under(payload,relative),destination);
            }
        }
        catch
        {
            changed.Reverse();
            foreach (string relative in changed)
            {
                string destination=Under(home,relative);
                if (existed.Contains(relative)) Replace(Under(backup,relative),destination);
                else if (File.Exists(destination)) File.Delete(destination);
            }
            throw;
        }
    }

    static void Replace(string source,string destination)
    {
        string pending=destination+".BRAIN-new-"+Guid.NewGuid().ToString("N");
        try
        {
            File.Copy(source,pending);
            if (File.Exists(destination)) File.Replace(pending,destination,null);
            else File.Move(pending,destination);
        }
        finally { if (File.Exists(pending)) File.Delete(pending); }
    }

    internal static void Apply(string[] args)
    {
        string home=null;
        try
        {
            if (args.Length!=5) throw new Exception("更新の引数が正しくありません。");
            home=Path.GetFullPath(args[1]); string stage=Path.GetFullPath(args[2]);
            if (!File.Exists(Path.Combine(home,"BRAIN.exe")) || !File.Exists(Path.Combine(home,"version.json"))) throw new Exception("BRAINの保存先が正しくありません。");
            WaitForExit(Int32.Parse(args[3]));
            int runningPid=Int32.Parse(args[4]); CloseApplication(runningPid); WaitForExit(runningPid);
            string lockName;
            using (var sha=SHA256.Create()) lockName="Local\\BRAINUpdate-"+BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(home.ToUpperInvariant()))).Replace("-","");
            using (var mutex=new Mutex(false,lockName))
            {
                if (!mutex.WaitOne(0)) throw new Exception("別の更新が進行中です。");
                try { Install(home,stage,delegate(string file) {}); }
                finally { mutex.ReleaseMutex(); }
            }
            DeleteStage(home,stage);
        }
        catch (Exception error)
        {
            MessageBox.Show("更新できませんでした。元のファイルへ復元します。\n"+error.Message,"BRAIN — アップデート",MessageBoxButtons.OK,MessageBoxIcon.Error);
        }
        finally
        {
            if (home!=null && File.Exists(Path.Combine(home,"BRAIN.exe")))
                Process.Start(new ProcessStartInfo(Path.Combine(home,"BRAIN.exe"),"--skip-update-check") {WorkingDirectory=home,UseShellExecute=false});
        }
    }
}
