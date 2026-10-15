// Real WKWebView snapshots of the unmodified product page. No image compositing.
import AppKit
import WebKit
import Foundation

struct Input: Decodable {
    let url: String
    let output: String
    let language: String
    let setup: Bool
}
let input=try JSONDecoder().decode(Input.self,from:FileHandle.standardInput.readDataToEndOfFile())
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
let config=WKWebViewConfiguration()
config.applicationNameForUserAgent="ToolbeltDocumentation"
config.websiteDataStore = .nonPersistent()
let view=WKWebView(frame:NSRect(x:0,y:0,width:340,height:500),configuration:config)
let window=NSWindow(contentRect:view.frame,styleMask:[.borderless],backing:.buffered,defer:false)
window.contentView=view
window.appearance=NSAppearance(named:.darkAqua)
var phase=0,elapsed=0,busy=false,saved:[String]=[]
func finish(_ ok:Bool,_ reason:String="") {
    let result:[String:Any]=["passed":ok,"language":input.language,"screenshots":saved,"reason":reason]
    let data=try! JSONSerialization.data(withJSONObject:result,options:.sortedKeys)
    print(String(data:data,encoding:.utf8)!)
    exit(ok ? 0 : 1)
}
func action(_ code:String) {
    view.evaluateJavaScript(code) {_,error in
        busy=false
        if error != nil {finish(false,"UI action script failed")}
    }
}
func capture(_ name:String,then code:String,next:Int) {
    let snapshot=WKSnapshotConfiguration();snapshot.rect=view.bounds
    view.takeSnapshot(with:snapshot) {image,error in
        guard let tiff=image?.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),let png=bitmap.representation(using:.png,properties:[:]) else {finish(false,"Snapshot failed");return}
        let path=URL(fileURLWithPath:input.output).appendingPathComponent(name+".png")
        do {try png.write(to:path)} catch {finish(false,"Snapshot write failed");return}
        saved.append(name+".png");phase=next
        if code.isEmpty {finish(true)} else {action(code)}
    }
}
let read="""
JSON.stringify({language:document.documentElement.lang,nativeLanguage:navigator.language,
status:typeof state!=='undefined'?state?.status:null,
busy:typeof busy!=='undefined'?busy:true,
rows:typeof state!=='undefined'?(state?.sessions||[]):[],
form:document.getElementById('form').hidden===false,
setup:document.getElementById('setup').hidden===false,
error:document.getElementById('error').hidden===false,
text:document.body.innerText})
"""
func clickRow(_ name:String,_ selector:String)->String {
    return "document.querySelector('[data-session-name=\""+name+"\"] "+selector+"').click()"
}
let timer=Timer.scheduledTimer(withTimeInterval:0.25,repeats:true) {_ in
    elapsed += 1
    if elapsed>240 {finish(false,"Timed out awaiting localized UI")}
    if busy {return};busy=true
    view.evaluateJavaScript(read) {value,error in
        guard let value=value as? String,let data=value.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else {busy=false;return}
        guard state["status"] as? String == "online",state["busy"] as? Bool == false else {busy=false;return}
        if state["error"] as? Bool == true {finish(false,"Product page shows an error");return}
        if state["language"] as? String != input.language {finish(false,"Native process language did not reach WebView: "+(state["nativeLanguage"] as? String ?? "unknown"));return}
        if input.language=="en",let text=state["text"] as? String,text.range(of:"[\u{4e00}-\u{9fff}]",options:.regularExpression) != nil {finish(false,"English UI contains untranslated text");return}
        if input.setup {
            if state["setup"] as? Bool == true {capture("10-remote-setup",then:"",next:99)} else {busy=false}
            return
        }
        let rows=state["rows"] as? [[String:Any]] ?? []
        let names=rows.compactMap{$0["name"] as? String}
        let current=rows.first{$0["current"] as? Bool == true}?["name"] as? String
        switch phase {
        case 0 where rows.count==3:
            capture("01-sessions",then:"document.getElementById('create').click()",next:1)
        case 1 where state["form"] as? Bool == true:
            capture("02-create",then:"document.getElementById('field').value='new-task';document.getElementById('submit').click()",next:2)
        case 2 where names.contains("new-task") && current==nil:
            capture("03-created",then:clickRow("new-task",".select"),next:3)
        case 3 where current=="new-task":
            capture("04-switch",then:clickRow("new-task",".actions button"),next:4)
        case 4 where state["form"] as? Bool == true:
            capture("05-rename",then:"document.getElementById('field').value='focus-task';document.getElementById('submit').click()",next:5)
        case 5 where names.contains("focus-task") && current=="focus-task":
            capture("06-renamed",then:clickRow("focus-task",".danger"),next:6)
        case 6 where !names.contains("focus-task") && rows.count==3:
            capture("07-delete",then:"document.getElementById('detach').click()",next:7)
        case 7 where current==nil:
            capture("08-return-ssh",then:"document.querySelector('.session .danger').click()",next:8)
        case 8 where rows.isEmpty:
            capture("09-empty",then:"",next:99)
        case 8:
            action("document.querySelector('.session .danger').click()")
        default:busy=false
        }
    }
}
view.load(URLRequest(url:URL(string:input.url)!))
RunLoop.main.run()
