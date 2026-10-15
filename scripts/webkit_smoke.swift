// Native WKWebView smoke test. Drives only a caller-supplied isolated fixture.
import AppKit
import WebKit
import Foundation

struct Input: Decodable {
    let url: String
    let width: Double?
    let appearance: String?
    let screenshot: String?
    let crud: Bool?
    let roundtrip: Bool?
    let setup: Bool?
    let themeCycle: Bool?
    let expectedPane: String?
    let expectedSocket: String?
    let expectedServer: String?
}
let input = try JSONDecoder().decode(Input.self, from: FileHandle.standardInput.readDataToEndOfFile())
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let config = WKWebViewConfiguration()
config.applicationNameForUserAgent = "ToolbeltQA"
config.websiteDataStore = .nonPersistent()
config.preferences.javaScriptCanOpenWindowsAutomatically = false
let view = WKWebView(frame:NSRect(x:0,y:0,width:input.width ?? 240,height:440),configuration:config)
let window = NSWindow(contentRect:view.frame,styleMask:[.borderless],backing:.buffered,defer:false)
window.contentView = view
if let appearance=input.appearance {
    window.appearance=NSAppearance(named:appearance == "dark" ? .darkAqua : .aqua)
}
var elapsed=0, phase=0, busy=false, resumeAfter=0
var results:[String:Any] = [:]
var firstBackground:String?
func finish(_ success: Bool) {
    results["passed"]=success
    results["phase"]=phase
    func output() {
        if let data=try? JSONSerialization.data(withJSONObject:results,options:[.sortedKeys]), let text=String(data:data,encoding:.utf8) {print(text)}
        exit(success ? 0 : 1)
    }
    guard let path=input.screenshot else {output(); return}
    let snapshot=WKSnapshotConfiguration()
    snapshot.rect=view.bounds
    view.takeSnapshot(with:snapshot) {image,error in
        if let tiff=image?.tiffRepresentation, let bitmap=NSBitmapImageRep(data:tiff), let png=bitmap.representation(using:.png,properties:[:]) {
            do {try png.write(to:URL(fileURLWithPath:path));results["screenshot_written"]=true} catch {results["screenshot_written"]=false}
        } else {results["screenshot_written"]=false}
        output()
    }
}
let read = """
JSON.stringify({scopePane:typeof state !== 'undefined' ? state?.target?.pane : null,
scopeSocket:typeof state !== 'undefined' ? state?.target?.socket : null,
scopeServer:typeof state !== 'undefined' ? state?.target?.server_pid : null,
appearance:document.documentElement.dataset.appearance,
colorScheme:getComputedStyle(document.documentElement).colorScheme,
background:getComputedStyle(document.body).backgroundColor,
foreground:getComputedStyle(document.body).color,
systemPalette:CSS.supports('color','-apple-system-label'),
setupVisible:document.getElementById('setup')?.hidden===false,
setupAvailable:document.getElementById('setup-install')?.hidden===false,
setupKind:typeof state !== 'undefined' ? state?.setup?.kind : null,
feedback:document.getElementById('feedback')?.textContent,
status:typeof state !== 'undefined' ? state?.status : null,
error:document.getElementById('error')?.textContent,errorHidden:document.getElementById('error')?.hidden,
formOpen:document.getElementById('form')?.hidden===false,
rows:typeof state !== 'undefined' ? (state?.sessions || []).map(r=>r.name+(r.current ? ' · current' : '')) : [],
bodyWidth:document.body.clientWidth,scrollWidth:document.body.scrollWidth,
font:getComputedStyle(document.body).fontSize,
buttonHeight:document.getElementById('create')?.getBoundingClientRect().height,
headings:document.querySelectorAll('h1').length})
"""
let timer = Timer.scheduledTimer(withTimeInterval:0.4,repeats:true) {timer in
    elapsed += 1
    if busy {return}
    busy=true
    view.evaluateJavaScript(read) {value,error in
        busy=false
        guard let value=value as? String,let data=value.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else {
            if elapsed>100 {timer.invalidate();results["error"]="WebView did not load";finish(false)}
            return
        }
        if let pane=input.expectedPane, let socket=input.expectedSocket {
            let valid=(state["scopePane"] as? String == pane && state["scopeSocket"] as? String == socket && (input.expectedServer == nil || state["scopeServer"] as? String == input.expectedServer))
            if !valid {
                if elapsed>100 {timer.invalidate();results["error"]="Isolated QA scope is not active; no actions sent";finish(false)}
                return
            }
        }
        let status=state["status"] as? String ?? ""
        let rows=state["rows"] as? [String] ?? []
        let errorText=state["error"] as? String ?? ""
        let hidden=state["errorHidden"] as? Bool ?? false
        if !hidden && !errorText.isEmpty {
            timer.invalidate();results["error"]="WebView displayed a request error";finish(false);return
        }
        if input.themeCycle == true {
            if state["appearance"] as? String == "dark" {firstBackground=state["background"] as? String}
            if let prior=firstBackground, state["appearance"] as? String == "light", let bg=state["background"] as? String, bg != prior {
                timer.invalidate();results["dom"]=state;results["theme_cycle"]=true;results["initial_background"]=prior;finish(true);return
            }
            if elapsed>60 {timer.invalidate();results["error"]="Theme failed to follow API appearance";results["dom"]=state;finish(false)}
            return
        }
        if input.setup == true {
            if phase==0 && state["setupAvailable"] as? Bool == true {
                view.evaluateJavaScript("document.getElementById('setup-install').click()",completionHandler:nil);phase=1
            } else if phase==1 && state["setupKind"] as? String == "reconnect", let feedback=state["feedback"] as? String, !feedback.isEmpty {
                timer.invalidate();results["dom"]=state;results["setup_click"]=true;finish(true);return
            }
            if elapsed>60 {timer.invalidate();results["error"]="Setup action failed to render";results["dom"]=state;finish(false)}
            return
        }
        if input.crud != true && (status == "online" || status == "unsupported") {
            timer.invalidate();results["dom"]=state;finish(true);return
        }
        var action:String?
        switch phase {
        case 0 where status == "online": action="document.getElementById('create').click()";phase=1
        case 1 where state["formOpen"] as? Bool == true:
            action="document.getElementById('field').value='webkit-new';document.getElementById('submit').click()";phase=2
        case 2 where rows.contains(where:{$0.hasPrefix("webkit-new")}):
            action="document.querySelector('[data-session-name=\"webkit-new\"] .actions button').click()";phase=3
        case 3 where state["formOpen"] as? Bool == true:
            action="document.getElementById('field').value='webkit-renamed';document.getElementById('submit').click()";phase=4
        case 4 where rows.contains(where:{$0.hasPrefix("webkit-renamed")}):
            action="Array.from(document.querySelectorAll('.session')).find(n=>n.querySelector('.name').textContent.startsWith('webkit-renamed')).querySelector('.select').click()";phase=5
        case 5 where rows.contains(where:{$0.hasPrefix("webkit-renamed") && $0.contains(" · current")}):
            action="document.querySelector('[data-session-name=\"webkit-renamed\"] .danger').click()";phase=6
        case 6 where status == "online" && !rows.contains(where:{$0.hasPrefix("webkit-renamed")}):
            if input.roundtrip == true {
                action="Array.from(document.querySelectorAll('.session')).find(n=>n.querySelector('.name').textContent.startsWith('qa-two')).querySelector('.select').click()";phase=7
            } else {timer.invalidate();results["dom"]=state;results["crud"]=true;finish(true);return}
        case 7 where rows.contains(where:{$0.hasPrefix("qa-two") && $0.contains(" · current")}):
            action="document.getElementById('detach').click()";phase=8;resumeAfter=elapsed+8
        case 8 where !rows.contains(where:{$0.contains(" · current")}) && elapsed>=resumeAfter:
            action="Array.from(document.querySelectorAll('.session')).find(n=>n.querySelector('.name').textContent.startsWith('qa-base')).querySelector('.select').click()";phase=9
        case 9 where rows.contains(where:{$0.hasPrefix("qa-base") && $0.contains(" · current")}):
            action="document.getElementById('detach').click()";phase=10;resumeAfter=elapsed+8
        case 10 where !rows.contains(where:{$0.contains(" · current")}) && elapsed>=resumeAfter:
            timer.invalidate();results["dom"]=state;results["crud"]=true;results["roundtrip"]=true;finish(true);return
        default: break
        }
        if let action=action {view.evaluateJavaScript(action,completionHandler:nil)}
        if elapsed>100 {timer.invalidate();results["error"]="Timed out awaiting fixture UI";results["dom"]=state;finish(false)}
    }
}
view.load(URLRequest(url:URL(string:input.url)!))
RunLoop.main.run()
