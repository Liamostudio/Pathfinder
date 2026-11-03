"""Native integration check. Run with system Python and an active desktop session.

Checks the actual GTK/WebKit window, live local backend, trained scene and WebGL2.
It does not open or automate a user browser. It exits and cleans up its backend.
"""
import json
import base64
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from gaussian_studio.desktop import create_application
import gi
gi.require_version('Gtk','3.0');gi.require_version('WebKit2','4.1')
from gi.repository import GLib,Gtk

application=create_application(ROOT/'gaussian_studio/data')
application.set_application_id('io.pathfinder.SplatStudio.SmokeTest')
checks={'passed':False};output=ROOT/'gaussian_studio/data/desktop-check.json'
snapshot=ROOT/'gaussian_studio/data/desktop-render.png'

def inspect():
    if application.view is None:return True
    def result(view,task,_):
        try:
            state=json.loads(view.evaluate_javascript_finish(task).to_string())
            if not state['loaded']:return
            rendered=state.pop('render')
            native=isinstance(application.window,Gtk.ApplicationWindow) and application.window.get_visible() and application.window.get_realized()
            snapshot.write_bytes(base64.b64decode(rendered.split(',',1)[1]))
            checks.update(state,native_window=native,passed=bool(state['webgl2'] and state['gaussians']!='—' and native and snapshot.stat().st_size>8000))
            output.write_text(json.dumps(checks,indent=2));print(json.dumps(checks),flush=True)
            application.quit()
        except Exception as exc:
            checks['error']=str(exc);print(str(exc),flush=True);application.quit()
    application.view.evaluate_javascript("JSON.stringify({loaded:document.getElementById('viewerStatus')?.textContent.includes('Gaussians'),title:document.title,webgl2:!!document.querySelector('canvas')?.getContext('webgl2'),gaussians:document.getElementById('gaussians')?.textContent,render:document.querySelector('canvas')?.toDataURL('image/png'),backend:document.getElementById('backend')?.value,key_field_empty:document.getElementById('apiKey')?.value.length===0,external_upload_unapproved:document.getElementById('remoteConsent')?.checked===false,quality_options:Array.from(document.getElementById('steps')?.options||[]).map(o=>o.textContent)})",-1,None,None,None,result,None)
    return True

def timeout():
    if not checks['passed']:checks['error']='Desktop scene load timed out';print(json.dumps(checks),flush=True)
    application.quit();return False

GLib.timeout_add_seconds(2,inspect);GLib.timeout_add_seconds(30,timeout)
application.run([str(Path(__file__))])
if application.backend.process and application.backend.process.poll() is None:raise SystemExit('Desktop backend was not cleaned up')
raise SystemExit(0 if checks['passed'] else 1)
