import Cocoa
import WebKit
import UniformTypeIdentifiers
import Darwin

/// Only the app's randomly assigned loopback origin may supply a saved archive.
private final class LocalDownloadDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let origin: URL

    init(origin: URL) { self.origin = origin }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard let url = request.url,
              url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port,
              url.user == nil, url.password == nil else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}

@MainActor
final class CrateApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var loadingView: NSView!
    private var loadingLabel: NSTextField!
    private var backend: Process?
    private var backendGroup: pid_t?
    private var backendOrigin: URL?
    private var readinessTimer: Timer?
    private var launchTime = Date()
    private var temporaryDirectory: URL?
    private var logHandle: FileHandle?
    private var quitting = false
    private var reportingFailure = false
    private var savingZIP = false
    private var activeSavePanel: NSSavePanel?
    private var copyInProgress = false
    private var backendShutdownFinished = false
    private var terminationReplied = false
    private var downloadSession: URLSession?
    private var downloadTask: URLSessionDownloadTask?

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenus()
        makeWindow()
        startBackend()
    }

    private func makeMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Crate", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Crate", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Crate", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        let refresh = viewMenu.addItem(withTitle: "Refresh", action: #selector(refreshPage), keyEquivalent: "r")
        refresh.target = self
        viewItem.submenu = viewMenu
        main.addItem(viewItem)

        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = main
    }

    private func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Crate"            // still used by Mission Control and the Window menu
        window.titleVisibility = .hidden  // but not drawn in the title bar
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: 620, height: 620)
        window.isReleasedWhenClosed = false
        window.backgroundColor = NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? NSColor(calibratedRed: 6 / 255, green: 7 / 255, blue: 12 / 255, alpha: 1)
                : NSColor(calibratedRed: 242 / 255, green: 244 / 255, blue: 251 / 255, alpha: 1)
        }
        window.setFrameAutosaveName("CrateMainWindow")
        window.center()

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(self, name: "crate")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.isHidden = true

        let content = NSView()
        window.contentView = content
        content.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor)
        ])

        let mark = NSTextField(labelWithString: "crate.")
        mark.font = .systemFont(ofSize: 46, weight: .bold)
        mark.textColor = NSColor(calibratedRed: 237 / 255, green: 91 / 255, blue: 45 / 255, alpha: 1)
        loadingLabel = NSTextField(labelWithString: "Getting your music space ready…")
        loadingLabel.font = .systemFont(ofSize: 14)
        loadingLabel.textColor = .secondaryLabelColor
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.startAnimation(nil)
        let stack = NSStackView(views: [mark, loadingLabel, spinner])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        loadingView = stack
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: content.centerYAnchor)
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func startBackend() {
        do {
            guard let resources = Bundle.main.resourceURL else {
                throw nativeError("The application resources could not be found.")
            }
            let executable = resources.appendingPathComponent("backend/crate-server")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw nativeError("Crate’s bundled music engine is missing. Please use the complete Crate application.")
            }
            let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("Crate-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            temporaryDirectory = temporary
            let caches = try FileManager.default.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            let downloads = caches.appendingPathComponent("Crate/downloads", isDirectory: true)
            try FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
            let ready = temporary.appendingPathComponent("ready.json")
            let logURL = temporary.appendingPathComponent("engine.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            logHandle = try FileHandle(forWritingTo: logURL)

            let process = Process()
            process.executableURL = executable
            process.currentDirectoryURL = executable.deletingLastPathComponent()
            process.arguments = ["--ready-file", ready.path, "--data-dir", downloads.path, "--parent-pid", String(getpid())]
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONUNBUFFERED"] = "1"
            process.environment = environment
            process.standardOutput = logHandle
            process.standardError = logHandle
            process.terminationHandler = { [weak self] process in
                DispatchQueue.main.async {
                    guard let self, !self.quitting else { return }
                    self.startupFailure("Crate’s music engine stopped unexpectedly. Quit and open Crate again to reconnect.")
                }
            }
            backend = process
            launchTime = Date()
            try process.run()
            readinessTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.checkReadiness(at: ready) }
            }
        } catch {
            startupFailure(error.localizedDescription)
        }
    }

    private func checkReadiness(at file: URL) {
        guard !quitting, !reportingFailure else { return }
        if let data = try? Data(contentsOf: file),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let address = object["url"] as? String,
           let url = URL(string: address),
           url.scheme == "http", url.host == "127.0.0.1",
           let port = url.port, (1...65535).contains(port),
           url.user == nil, url.password == nil,
           url.path.isEmpty || url.path == "/" {
            readinessTimer?.invalidate()
            readinessTimer = nil
            if let pid = object["pid"] as? Int, pid > 1, pid <= Int(Int32.max) {
                let candidate = pid_t(pid)
                // The backend calls setsid(); never signal the application's own group.
                if getpgid(candidate) == candidate && candidate != getpgrp() { backendGroup = candidate }
            }
            backendOrigin = url
            loadingLabel.stringValue = "Opening your collection…"
            webView.load(URLRequest(url: url))
        } else if Date().timeIntervalSince(launchTime) > 30 {
            startupFailure("Crate took too long to start. Quit and open the application again.")
        }
    }

    private func sameOrigin(_ url: URL) -> Bool {
        guard let origin = backendOrigin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port && url.user == nil && url.password == nil
    }

    /// The page asks for a folder; the native Finder panel answers, so a path is never typed by hand.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "crate", message.frameInfo.isMainFrame,
              sameOrigin(message.frameInfo.request.url ?? URL(fileURLWithPath: "/")),
              let body = message.body as? [String: Any], body["type"] as? String == "chooseFolder" else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose a music folder"
        panel.message = "Your songs will be saved here."
        panel.prompt = "Choose"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        let music = FileManager.default.urls(for: .musicDirectory, in: .userDomainMask).first
        if let current = body["current"] as? String, !current.isEmpty, FileManager.default.fileExists(atPath: current) {
            panel.directoryURL = URL(fileURLWithPath: current, isDirectory: true)
        } else {
            panel.directoryURL = music
        }
        panel.beginSheetModal(for: window) { [weak self] response in
            let chosen: Any = response == .OK ? (panel.url?.path ?? NSNull()) : NSNull()
            let encoded = (try? JSONSerialization.data(withJSONObject: [chosen], options: []))
                .flatMap { String(data: $0, encoding: .utf8) } ?? "[null]"
            self?.webView.evaluateJavaScript("window.CrateFolderPicked && window.CrateFolderPicked(\(encoded)[0])", completionHandler: nil)
        }
    }

    private func isArchiveURL(_ url: URL) -> Bool {
        let components = url.path.split(separator: "/")
        return components.count == 4 && components[0] == "api" && components[1] == "jobs" && components[3] == "download"
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        guard sameOrigin(url) else {
            decisionHandler(.cancel)
            if navigationAction.navigationType == .linkActivated && url.scheme == "https" {
                NSWorkspace.shared.open(url)
            }
            return
        }
        if isArchiveURL(url) {
            decisionHandler(.cancel)
            saveArchive(from: url)
        } else if navigationAction.targetFrame == nil {
            decisionHandler(.cancel)
            webView.load(navigationAction.request)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url, sameOrigin(url) else {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadingView.isHidden = true
        webView.isHidden = false
        window.makeFirstResponder(webView)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(error)
    }

    private func navigationFailed(_ error: Error) {
        if (error as NSError).code == NSURLErrorCancelled || quitting { return }
        let alert = NSAlert()
        alert.messageText = "Crate couldn’t open its music window"
        alert.informativeText = "Try refreshing the window. Your downloaded files are still on your Mac."
        alert.addButton(withTitle: "Refresh")
        alert.addButton(withTitle: "Quit")
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.refreshPage() }
            else { NSApp.terminate(nil) }
        }
    }

    @objc private func refreshPage() {
        guard let url = backendOrigin, !quitting else { return }
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
    }

    private func saveArchive(from url: URL) {
        guard !quitting, !savingZIP, let origin = backendOrigin, let temporary = temporaryDirectory else { return }
        savingZIP = true
        window.subtitle = "Preparing…"
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 600
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        let session = URLSession(configuration: configuration, delegate: LocalDownloadDelegate(origin: origin), delegateQueue: nil)
        downloadSession = session
        downloadTask = session.downloadTask(with: url) { [weak self] location, response, error in
            do {
                if let error { throw error }
                guard let response = response as? HTTPURLResponse, response.statusCode == 200, let location else {
                    throw NSError(domain: "Crate", code: 1, userInfo: [NSLocalizedDescriptionKey: "This ZIP is no longer available. Return to your playlist and pack it again."])
                }
                // URLSession deletes its temporary file when this callback returns.
                let suggested = response.suggestedFilename ?? "My playlist.zip"
                let staged = temporary.appendingPathComponent("archive-\(UUID().uuidString).\(self?.saveExtension(suggested) ?? "zip")")
                try FileManager.default.moveItem(at: location, to: staged)
                DispatchQueue.main.async {
                    guard let self, !self.quitting else { try? FileManager.default.removeItem(at: staged); return }
                    self.downloadTask = nil
                    self.downloadSession?.finishTasksAndInvalidate()
                    self.downloadSession = nil
                    self.window.subtitle = ""
                    self.presentSavePanel(for: staged, suggestedName: suggested)
                }
            } catch {
                let message = error.localizedDescription
                DispatchQueue.main.async {
                    guard let self, !self.quitting else { return }
                    self.finishDownload()
                    self.showError("Couldn’t save the file", message: message)
                }
            }
        }
        downloadTask?.resume()
    }

    private static let audioExtensions: Set<String> = ["zip", "mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "aif", "aiff", "mka", "webm", "mp4"]

    /// ZIPs for playlists, or the song's own audio extension for a single song.
    private func saveExtension(_ suggestion: String) -> String {
        let ext = (suggestion as NSString).pathExtension.lowercased()
        return CrateApp.audioExtensions.contains(ext) ? ext : "zip"
    }

    private func archiveName(_ suggestion: String) -> String {
        let leaf = (suggestion as NSString).lastPathComponent
        let cleaned = String(leaf.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) })
            .replacingOccurrences(of: ":", with: "-")
            .replacingOccurrences(of: "\\", with: "-")
            .trimmingCharacters(in: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ".")))
        let ext = saveExtension(cleaned)
        let base = cleaned.lowercased().hasSuffix("." + ext) ? String(cleaned.dropLast(ext.count + 1)) : cleaned
        return (base.isEmpty ? "My playlist" : String(base.prefix(180))) + "." + ext
    }

    private func presentSavePanel(for archive: URL, suggestedName: String) {
        guard !quitting else {
            try? FileManager.default.removeItem(at: archive)
            return
        }
        let panel = NSSavePanel()
        activeSavePanel = panel
        let ext = saveExtension(suggestedName)
        panel.title = ext == "zip" ? "Save your playlist" : "Save your song"
        panel.message = ext == "zip" ? "All your tracks, together in one ZIP." : "Choose where to save your song."
        panel.prompt = ext == "zip" ? "Save ZIP" : "Save"
        panel.canCreateDirectories = true
        panel.allowedContentTypes = [UTType(filenameExtension: ext) ?? .zip]
        panel.isExtensionHidden = false
        panel.nameFieldStringValue = archiveName(suggestedName)
        // Open as the full Finder-style browser (sidebar, columns, new folder) rather than the collapsed
        // "Where:" sheet, and start in the folder used last time.
        UserDefaults.standard.set(true, forKey: "NSNavPanelExpandedStateForSaveMode")
        panel.showsTagField = false
        if let remembered = UserDefaults.standard.string(forKey: "CrateLastSaveFolder"),
           FileManager.default.fileExists(atPath: remembered) {
            panel.directoryURL = URL(fileURLWithPath: remembered, isDirectory: true)
        } else {
            panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        }
        panel.beginSheetModal(for: window) { [weak self] response in
            panel.orderOut(nil)
            guard let self else { try? FileManager.default.removeItem(at: archive); return }
            self.activeSavePanel = nil
            guard !self.quitting else { try? FileManager.default.removeItem(at: archive); return }
            guard response == .OK, let destination = panel.url else {
                try? FileManager.default.removeItem(at: archive)
                self.finishDownload()
                return
            }
            UserDefaults.standard.set(destination.deletingLastPathComponent().path, forKey: "CrateLastSaveFolder")
            // An existing path has just been confirmed by the native Save panel.
            let replacementApproved = FileManager.default.fileExists(atPath: destination.path)
            self.window.subtitle = "Saving…"
            // Once Save is accepted, quitting must let this copy commit or clean up.
            self.copyInProgress = true
            DispatchQueue.global(qos: .userInitiated).async {
                let incoming = destination.deletingLastPathComponent().appendingPathComponent(".Crate-\(UUID().uuidString).\(destination.pathExtension.isEmpty ? "zip" : destination.pathExtension)")
                do {
                    try FileManager.default.copyItem(at: archive, to: incoming)
                    if FileManager.default.fileExists(atPath: destination.path) {
                        guard replacementApproved else {
                            throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteFileExistsError, userInfo: [NSLocalizedDescriptionKey: "A file with that name appeared while saving. Please choose another name."])
                        }
                        _ = try FileManager.default.replaceItemAt(destination, withItemAt: incoming)
                    } else {
                        try FileManager.default.moveItem(at: incoming, to: destination)
                    }
                    try? FileManager.default.removeItem(at: archive)
                    DispatchQueue.main.async {
                        self.copyInProgress = false
                        self.finishDownload()
                        guard !self.quitting else {
                            self.finishTerminationIfReady()
                            return
                        }
                        NSWorkspace.shared.activateFileViewerSelecting([destination])
                    }
                } catch {
                    try? FileManager.default.removeItem(at: incoming)
                    let message = error.localizedDescription
                    DispatchQueue.main.async {
                        self.copyInProgress = false
                        guard !self.quitting else {
                            try? FileManager.default.removeItem(at: archive)
                            self.finishDownload()
                            self.finishTerminationIfReady()
                            return
                        }
                        self.window.subtitle = ""
                        let alert = NSAlert()
                        alert.messageText = "Choose another place to save your ZIP"
                        alert.informativeText = message
                        alert.addButton(withTitle: "Choose Location")
                        alert.addButton(withTitle: "Cancel")
                        alert.beginSheetModal(for: self.window) { response in
                            if response == .alertFirstButtonReturn {
                                self.presentSavePanel(for: archive, suggestedName: suggestedName)
                            } else {
                                try? FileManager.default.removeItem(at: archive)
                                self.finishDownload()
                            }
                        }
                    }
                }
            }
        }
    }

    private func finishDownload() {
        savingZIP = false
        downloadTask = nil
        downloadSession?.finishTasksAndInvalidate()
        downloadSession = nil
        window.subtitle = ""
    }

    private func nativeError(_ message: String) -> NSError {
        NSError(domain: "Crate", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    private func showError(_ title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    private func startupFailure(_ message: String) {
        guard !reportingFailure, !quitting else { return }
        reportingFailure = true
        readinessTimer?.invalidate()
        readinessTimer = nil
        let alert = NSAlert()
        alert.messageText = "Crate couldn’t start"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Quit")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return terminationReplied ? .terminateNow : .terminateLater }
        quitting = true
        activeSavePanel?.cancel(nil)
        if copyInProgress { window.subtitle = "Finishing your ZIP before closing…" }
        readinessTimer?.invalidate()
        downloadTask?.cancel()
        downloadSession?.invalidateAndCancel()
        webView?.stopLoading()

        // Capture the group even when the window closes before readiness is written.
        if backendGroup == nil, let process = backend, process.isRunning {
            let pid = process.processIdentifier
            if pid > 1, getpgid(pid) == pid, pid != getpgrp() { backendGroup = pid }
        }
        if let group = backendGroup { kill(-group, SIGTERM) }
        else if let process = backend, process.isRunning { process.terminate() }

        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [self] in
            if let group = backendGroup, kill(-group, 0) == 0 { kill(-group, SIGKILL) }
            if let process = backend, process.isRunning { kill(process.processIdentifier, SIGKILL) }
            backendShutdownFinished = true
            finishTerminationIfReady()
        }
        return .terminateLater
    }

    private func finishTerminationIfReady() {
        guard quitting, backendShutdownFinished, !copyInProgress, !terminationReplied else { return }
        terminationReplied = true
        try? logHandle?.close()
        if let temporaryDirectory { try? FileManager.default.removeItem(at: temporaryDirectory) }
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let group = backendGroup { kill(-group, SIGTERM) }
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = CrateApp()
    application.setActivationPolicy(.regular)
    application.delegate = delegate
    withExtendedLifetime(delegate) { application.run() }
}
