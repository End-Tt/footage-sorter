// 素材分类 — desktop shell around the Python engine (Contents/Resources/engine/run_all.py).
// The engine and the AI CLIs (codex / claude) run through a login zsh, because apps opened from Finder
// do not inherit the user's shell PATH (Homebrew, npm, nvm …).
import AppKit
import SwiftUI

struct Stage: Identifiable {
    let id: String
    let label: String
    var done = 0
    var total = 0
    var state = 0  // 0 waiting, 1 running, 2 finished, 3 failed
}

final class Runner: ObservableObject {
    @Published var src = ""
    @Published var out = ""
    @Published var event = ""
    @Published var backend = UserDefaults.standard.string(forKey: "backend") ?? "codex"
    @Published var effort = UserDefaults.standard.string(forKey: "effort") ?? "medium"
    @Published var organize = UserDefaults.standard.string(forKey: "organize") ?? "copy"
    @Published var makeFCP = UserDefaults.standard.bool(forKey: "makeFCP")
    @Published var deps: [String: Any] = [:]
    @Published var depsChecked = false
    @Published var depsError = ""
    @Published var running = false
    @Published var stages: [Stage] = Runner.freshStages()
    @Published var log: [String] = []
    @Published var result: [String: String] = [:]
    @Published var finalMessage = ""
    private var proc: Process?
    private var outTouched = false

    static func freshStages() -> [Stage] {
        [Stage(id: "scan", label: "扫描"), Stage(id: "extract", label: "抽帧"), Stage(id: "classify", label: "AI 分类"),
         Stage(id: "check", label: "检查"), Stage(id: "build", label: "出成品"), Stage(id: "organize", label: "整理文件夹")]
    }

    var engine: String { (Bundle.main.resourcePath ?? ".") + "/engine" }

    /// /bin/zsh -lc 'exec "$0" "$@"' <args…>  — arguments are passed untouched (Chinese paths, spaces …)
    func zsh(_ args: [String]) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", "exec \"$0\" \"$@\""] + args
        p.currentDirectoryURL = URL(fileURLWithPath: NSHomeDirectory())
        return p
    }

    private var eventTouched = false

    func setSource(_ path: String) {
        guard path != src else { return }
        src = path
        let t = path.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, t != "/" else { return }
        let name = URL(fileURLWithPath: t).lastPathComponent
        if !outTouched { out = NSHomeDirectory() + "/Movies/素材分类/" + name }
        if !eventTouched { event = name }
    }

    func setOut(_ path: String) { guard path != out else { return }; out = path; outTouched = true }
    func setEvent(_ v: String) { guard v != event else { return }; event = v; eventTouched = true }

    func checkDeps() {
        depsChecked = false
        depsError = ""
        let p = zsh(["python3", engine + "/doctor.py", "--json"])
        let pipe = Pipe(), err = Pipe()
        p.standardOutput = pipe
        p.standardError = err
        p.terminationHandler = { _ in
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let e = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            DispatchQueue.main.async {
                if let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    self.deps = j
                    if (j["codex"] as? String ?? "").isEmpty && !(j["claude"] as? String ?? "").isEmpty { self.backend = "claude" }
                } else {
                    self.deps = [:]
                    self.depsError = e.contains("not found") || e.contains("找不到") || e.isEmpty
                        ? "没找到 Python 3。打开「终端」输入 xcode-select --install，装好后重开本程序。" : e
                }
                self.depsChecked = true
            }
        }
        try? p.run()
    }

    var missing: [String] {
        var m: [String] = []
        if !depsError.isEmpty { return ["Python 3"] }
        if !(deps["python_ok"] as? Bool ?? false) { m.append("Python 3.9+") }
        if (deps["ffmpeg"] as? String ?? "").isEmpty || (deps["ffprobe"] as? String ?? "").isEmpty { m.append("ffmpeg") }
        if !(deps["pillow"] as? Bool ?? false) { m.append("Pillow") }
        if (deps["codex"] as? String ?? "").isEmpty && (deps["claude"] as? String ?? "").isEmpty { m.append("Codex 或 Claude Code") }
        return m
    }

    func installPillow() {
        append("正在安装 Pillow…")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", "python3 -m pip install --user pillow || { command -v brew >/dev/null && brew install pillow; }"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        p.terminationHandler = { _ in
            let t = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            DispatchQueue.main.async {
                self.append(t.split(separator: "\n").suffix(3).joined(separator: "\n"))
                self.checkDeps()
            }
        }
        try? p.run()
    }

    func append(_ s: String) {
        log.append(s)
        if log.count > 400 { log.removeFirst(log.count - 400) }
    }

    func start() {
        guard !running, !src.isEmpty, !out.isEmpty else { return }
        for (k, v) in [("src", src), ("out", out), ("event", event), ("backend", backend), ("effort", effort), ("organize", organize)] {
            UserDefaults.standard.set(v, forKey: k)
        }
        UserDefaults.standard.set(makeFCP, forKey: "makeFCP")
        stages = Runner.freshStages().filter { organize != "none" || $0.id != "organize" }
        result = [:]
        finalMessage = ""
        log = []
        running = true
        let p = zsh(["python3", engine + "/run_all.py", "--src", src, "--out", out, "--event", event.isEmpty ? URL(fileURLWithPath: src).lastPathComponent : event,
                     "--backend", backend, "--effort", effort, "--organize", organize] + (makeFCP ? [] : ["--no-fcp"]))
        let pipe = Pipe(), err = Pipe()
        p.standardOutput = pipe
        p.standardError = err
        var buffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { return }
            buffer.append(d)
            while let r = buffer.firstRange(of: Data([0x0A])) {
                let line = buffer.subdata(in: 0..<r.lowerBound)
                buffer.removeSubrange(0..<r.upperBound)
                DispatchQueue.main.async { self.handle(line) }
            }
        }
        err.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { return }
            if let t = String(data: d, encoding: .utf8) {
                DispatchQueue.main.async {
                    for l in t.split(separator: "\n") where !l.contains("抽帧") && !l.contains("已读") { self.append(String(l)) }
                }
            }
        }
        p.terminationHandler = { pr in
            DispatchQueue.main.async {
                pipe.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                self.running = false
                if self.result.isEmpty && self.finalMessage.isEmpty {
                    self.finalMessage = pr.terminationStatus == 0 ? "结束" : "中途停止了（可以再点「开始整理」，做过的部分会跳过）"
                }
            }
        }
        do { try p.run(); proc = p } catch { running = false; append("启动失败：\(error)") }
    }

    func stop() {
        guard let p = proc, p.isRunning else { return }
        kill(-p.processIdentifier, SIGTERM)  // run_all.py made itself a process group
        p.terminate()
        append("已停止。")
    }

    private func handle(_ data: Data) {
        guard let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let st = j["stage"] as? String else {
            if let t = String(data: data, encoding: .utf8), !t.isEmpty { append(t) }
            return
        }
        if let msg = j["message"] as? String { append(msg) }
        if st == "done" {
            for i in stages.indices { stages[i].state = 2 }
            for k in ["index", "fcpxml", "out", "csv", "organized"] { if let v = j[k] as? String, !v.isEmpty { result[k] = v } }
            finalMessage = j["message"] as? String ?? "完成"
            return
        }
        if st == "error" {
            if let f = j["failed"] as? String, let i = stages.firstIndex(where: { $0.id == f }) { stages[i].state = 3 }
            finalMessage = "出错了：" + (j["message"] as? String ?? "")
            return
        }
        guard let i = stages.firstIndex(where: { $0.id == st }) else { return }
        for k in 0..<i where stages[k].state < 2 { stages[k].state = 2 }
        if stages[i].state < 2 { stages[i].state = 1 }
        if let d = j["done"] as? Int { stages[i].done = d }
        if let t = j["total"] as? Int { stages[i].total = t }
        if st == "check" || (st == "classify" && j["missing"] != nil) { stages[i].state = 2 }
    }
}

struct ContentView: View {
    @StateObject var r = Runner()

    func pickFolder(_ title: String, _ done: (String) -> Void) {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.canCreateDirectories = true
        p.message = title
        if p.runModal() == .OK, let u = p.url { done(u.path) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("素材分类").font(.largeTitle).bold()
                Text("AI 看缩略图给视频素材打标签，按场景整理进新文件夹，并生成目录网页和表格。原素材不会被改动。")
                    .foregroundStyle(.secondary).font(.callout)
            }
            GroupBox {
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 10) {
                    GridRow {
                        Text("素材文件夹")
                        TextField("选择要整理的视频文件夹", text: Binding(get: { r.src }, set: { r.setSource($0) }))
                            .accessibilityLabel("素材文件夹")
                        Button("选择…") { pickFolder("选择要整理的视频文件夹") { r.setSource($0) } }
                    }
                    GridRow {
                        Text("结果放在")
                        TextField("结果文件夹", text: Binding(get: { r.out }, set: { r.setOut($0) }))
                            .accessibilityLabel("结果放在")
                        Button("更改…") { pickFolder("选择结果放在哪里") { r.setOut($0) } }
                    }
                    GridRow {
                        Text("整理素材")
                        HStack {
                            Picker("", selection: $r.organize) {
                                Text("复制到新文件夹").tag("copy")
                                Text("只放替身").tag("link")
                                Text("不整理").tag("none")
                            }.pickerStyle(.segmented).frame(width: 330).labelsHidden()
                            Text(r.organize == "copy" ? "按场景分好，另有「精选」「废片」。和素材在同一块硬盘上时几乎不占空间"
                                 : r.organize == "link" ? "不占空间，但原素材以后不能挪位置" : "只出目录网页和表格")
                                .font(.caption).foregroundStyle(.secondary)
                            Spacer()
                        }
                        Text("")
                    }
                    GridRow {
                        Text("Final Cut Pro")
                        HStack {
                            Toggle("也生成 FCP 文件", isOn: $r.makeFCP)
                            if r.makeFCP {
                                TextField("导入后的事件名", text: Binding(get: { r.event }, set: { r.setEvent($0) })).accessibilityLabel("FCP 事件名")
                            }
                            Spacer()
                        }
                        Text("")
                    }
                    GridRow {
                        Text("AI")
                        HStack {
                            Picker("", selection: $r.backend) {
                                Text("Codex").tag("codex")
                                Text("Claude Code").tag("claude")
                            }.pickerStyle(.segmented).frame(width: 220).labelsHidden()
                            if r.backend == "codex" {
                                Picker("推理强度", selection: $r.effort) {
                                    Text("低").tag("low")
                                    Text("中（推荐）").tag("medium")
                                    Text("高").tag("high")
                                }.frame(width: 200)
                            }
                            Spacer()
                        }
                        Text("")
                    }
                }.padding(6)
            }
            GroupBox {
                HStack(spacing: 14) {
                    if !r.depsChecked {
                        ProgressView().controlSize(.small)
                        Text("检查环境…").foregroundStyle(.secondary)
                    } else if r.missing.isEmpty {
                        Label("环境齐全：" + (["Python", "ffmpeg", "Pillow"] + ((r.deps["codex"] as? String ?? "").isEmpty ? [] : ["Codex"])
                                                + ((r.deps["claude"] as? String ?? "").isEmpty ? [] : ["Claude Code"])).joined(separator: "、"),
                              systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else {
                        Label("缺少：" + r.missing.joined(separator: "、"), systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        if r.missing.contains("Pillow") && r.missing.count == 1 { Button("安装 Pillow") { r.installPillow() } }
                        if r.missing.contains("ffmpeg") { Text("在终端运行：brew install ffmpeg").font(.callout).textSelection(.enabled) }
                        if !r.depsError.isEmpty { Text(r.depsError).font(.callout).textSelection(.enabled) }
                    }
                    Spacer()
                    Button("重新检查") { r.checkDeps() }
                }.padding(4)
            }
            HStack {
                Button(action: { r.start() }) { Text(r.running ? "整理中…" : "开始整理").frame(minWidth: 120) }
                    .keyboardShortcut(.defaultAction).controlSize(.large)
                    .disabled(r.running || r.src.isEmpty || r.out.isEmpty || !r.depsChecked || !r.missing.isEmpty)
                Button("停止") { r.stop() }.disabled(!r.running)
                Spacer()
                Text("AI 分类会用你自己的 Codex / Claude 额度。实测 27 条约 1 分半钟。").font(.caption).foregroundStyle(.secondary)
            }
            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(r.stages) { s in
                        HStack {
                            Group {
                                switch s.state {
                                case 2: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                                case 3: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                                case 1: ProgressView().controlSize(.small)
                                default: Image(systemName: "circle").foregroundStyle(.secondary)
                                }
                            }.frame(width: 20)
                            Text(s.label).frame(width: 70, alignment: .leading)
                            if s.total > 0 {
                                ProgressView(value: Double(s.done), total: Double(max(s.total, 1)))
                                Text("\(s.done)/\(s.total)").monospacedDigit().frame(width: 70, alignment: .trailing)
                            } else { Spacer() }
                        }
                    }
                }.padding(4)
            }
            if !r.finalMessage.isEmpty {
                HStack {
                    Text(r.finalMessage).bold()
                    Spacer()
                    if let o = r.result["organized"] { Button("打开整理好的文件夹") { NSWorkspace.shared.open(URL(fileURLWithPath: o)) } }
                    if let idx = r.result["index"] { Button("打开目录网页") { NSWorkspace.shared.open(URL(fileURLWithPath: idx)) } }
                    if let o = r.result["out"] { Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: o)]) } }
                    if let f = r.result["fcpxml"], !f.isEmpty {
                        Button("导入 Final Cut Pro") {
                            let t = Process()
                            t.executableURL = URL(fileURLWithPath: "/usr/bin/open")
                            t.arguments = ["-a", "Final Cut Pro", f]
                            try? t.run()
                        }
                    }
                }
            }
            GroupBox("日志") {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(r.log.enumerated()), id: \.offset) { i, l in
                                Text(l).font(.system(.caption, design: .monospaced)).textSelection(.enabled).id(i)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .onChange(of: r.log.count) { _ in proxy.scrollTo(r.log.count - 1, anchor: .bottom) }
                }
            }.frame(minHeight: 120)
        }
        .padding(20)
        .frame(minWidth: 820, minHeight: 700)
        .onAppear { r.checkDeps() }
    }
}

@main
struct SucaiApp: App {
    var body: some Scene {
        WindowGroup("素材分类") { ContentView() }
            .windowResizability(.contentMinSize)
    }
}
