#!/usr/bin/env python3
"""GTK desktop window with an embedded, ephemeral WebKit renderer.

The system Python runs the desktop toolkit; the isolated CUDA Python runs the
backend and workers. No user browser, browser extension or hosted site is used.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import urllib.request

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT.parent/'workspace_splat/.venv/bin/python'
APP_ID='io.pathfinder.SplatStudio'


class DesktopBackend:
    """Own a backend on an OS-assigned loopback port; never stop another app."""
    def __init__(self,data,port=0):
        self.data=Path(data).resolve();self.port=port;self.process=None;self.url=None
        self.directory=None
        self.stop_lock=threading.Lock()

    def start(self,cancelled=None,timeout=25):
        if not RUNTIME.is_file():raise RuntimeError('The Gaussian runtime is missing. Run workspace_splat/bootstrap.py first.')
        self.directory=tempfile.TemporaryDirectory(prefix='splat-studio-desktop-')
        ready=Path(self.directory.name)/'ready.json'
        if cancelled and cancelled.is_set():self.stop();return
        try:
            with (ROOT/'desktop-backend.log').open('ab') as log:
                self.process=subprocess.Popen([str(RUNTIME),'-u','-m','gaussian_studio','--serve','--port',str(self.port),
                                               '--data',str(self.data),'--ready-file',str(ready)],
                                              cwd=ROOT.parent,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        except OSError:
            self.stop();raise RuntimeError('The local reconstruction engine could not be started. Check the Gaussian runtime and desktop-backend.log.') from None
        deadline=time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                if cancelled and cancelled.is_set():self.stop();return
                if self.process.poll() is not None:raise RuntimeError('The local backend exited. See gaussian_studio/desktop-backend.log.')
                if ready.is_file():
                    state=json.loads(ready.read_text())
                    if state.get('app')!='splat-studio' or state.get('pid')!=self.process.pid:raise RuntimeError('Invalid local backend readiness response.')
                    port=state.get('port')
                    if type(port) is not int or not 1<=port<=65535:raise RuntimeError('Invalid local backend port.')
                    url=f'http://127.0.0.1:{port}'
                    try:
                        with urllib.request.urlopen(url+'/api/health',timeout=1) as response:
                            if json.load(response).get('app')=='splat-studio':self.url=url;return url
                    except (OSError,ValueError):pass
                time.sleep(.1)
            raise RuntimeError('The local reconstruction engine did not become ready.')
        except BaseException:self.stop();raise

    def stop(self):
        with self.stop_lock:
            process=self.process
            if process is not None and process.poll() is None:
                # Workers are deliberately detached; finished/active captures survive app close.
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            if self.directory is not None:self.directory.cleanup();self.directory=None


def create_application(data,port=0):
    import gi
    gi.require_version('Gtk','3.0');gi.require_version('WebKit2','4.1')
    from gi.repository import Gtk,WebKit2,Gio,GLib,Gdk

    class SplatStudio(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID,flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.window=None;self.view=None;self.backend=DesktopBackend(data,port);self.backend_thread=None
            self.closing=threading.Event();self.ready=False

        def do_startup(self):
            Gtk.Application.do_startup(self)
            GLib.set_application_name('Splat Studio')
            for name,callback,shortcut in [('quit',lambda *_:self.quit(),'<Primary>q'),
                                           ('reload',lambda *_:self.view and self.view.reload(),'<Primary>r'),
                                           ('fullscreen',self.fullscreen,'F11')]:
                action=Gio.SimpleAction.new(name,None);action.connect('activate',callback);self.add_action(action)
                self.set_accels_for_action('app.'+name,[shortcut])

        def fullscreen(self,*_):
            if self.window.get_window().get_state()&Gdk.WindowState.FULLSCREEN:self.window.unfullscreen()
            else:self.window.fullscreen()

        def do_activate(self):
            if self.window is not None:self.window.present();return
            self.window=Gtk.ApplicationWindow(application=self,title='Splat Studio')
            self.window.set_default_size(1440,1000);self.window.set_size_request(720,540)
            self.window.set_icon_from_file(str(ROOT/'assets/splat-studio.svg'))
            header=Gtk.HeaderBar(title='Splat Studio',subtitle='Local Gaussian reconstruction',show_close_button=True)
            refresh=Gtk.Button.new_from_icon_name('view-refresh-symbolic',Gtk.IconSize.BUTTON)
            refresh.set_tooltip_text('Reload · Ctrl+R');refresh.set_action_name('app.reload');header.pack_end(refresh)
            self.window.set_titlebar(header)
            self.container=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.loading=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            self.loading.set_halign(Gtk.Align.CENTER);self.loading.set_valign(Gtk.Align.CENTER)
            spinner=Gtk.Spinner();spinner.start();self.loading.pack_start(spinner,False,False,0)
            self.loading.pack_start(Gtk.Label(label='Starting Splat Studio…'),False,False,0)
            self.container.pack_start(self.loading,True,True,0)
            self.window.add(self.container);self.window.show_all()
            self.backend_thread=threading.Thread(target=self.start_backend,daemon=True)
            self.backend_thread.start()

        def start_backend(self):
            try:
                url=self.backend.start(self.closing)
                if self.closing.is_set():self.backend.stop();return
                GLib.idle_add(self.open_studio,url)
            except Exception as exc:
                if not self.closing.is_set():GLib.idle_add(self.startup_failed,str(exc))

        def startup_failed(self,message):
            dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,message_type=Gtk.MessageType.ERROR,
                                     buttons=Gtk.ButtonsType.CLOSE,text='Splat Studio could not start')
            dialog.format_secondary_text(message);dialog.run();dialog.destroy();self.quit()
            return False

        def open_studio(self,url):
            if self.closing.is_set():self.backend.stop();return False
            context=WebKit2.WebContext.new_ephemeral()
            context.connect('download-started',self.download_started)
            self.view=WebKit2.WebView.new_with_context(context)
            settings=self.view.get_settings();settings.set_enable_webgl(True)
            settings.set_enable_html5_local_storage(False);settings.set_enable_developer_extras(False)
            self.view.connect('decide-policy',self.navigation_policy)
            self.view.connect('load-failed',self.load_failed)
            self.container.remove(self.loading);self.container.pack_start(self.view,True,True,0)
            self.view.load_uri(url+'/');self.window.show_all();self.ready=True
            return False

        def navigation_policy(self,view,decision,kind):
            if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):return False
            uri=decision.get_navigation_action().get_request().get_uri()
            parsed=urlsplit(uri);origin=urlsplit(self.backend.url)
            if (parsed.scheme,parsed.netloc)==(origin.scheme,origin.netloc):return False
            if parsed.scheme in ('about','blob'):return False
            decision.ignore()
            if parsed.scheme=='https':Gtk.show_uri_on_window(self.window,uri,Gdk.CURRENT_TIME)
            return True

        def load_failed(self,view,event,uri,error):
            if not self.closing.is_set():print('Desktop page failed to load:',error.message,flush=True)
            return False

        def download_started(self,context,download):
            download.connect('decide-destination',self.save_download)
            download.connect('failed',self.download_failed)

        def save_download(self,download,suggested_filename):
            chooser=Gtk.FileChooserNative.new('Save export',self.window,Gtk.FileChooserAction.SAVE,'Save','Cancel')
            chooser.set_do_overwrite_confirmation(True)
            chooser.set_current_name(Path(suggested_filename or 'splat-export').name)
            folder=GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            if folder and Path(folder).is_dir():chooser.set_current_folder(folder)
            if chooser.run()==Gtk.ResponseType.ACCEPT:
                destination=Path(chooser.get_filename()).as_uri()
                download.set_allow_overwrite(True);download.set_destination(destination)
            else:download.cancel()
            chooser.destroy();return True

        def download_failed(self,download,error):
            if self.closing.is_set() or error.matches(WebKit2.download_error_quark(),WebKit2.DownloadError.CANCELLED_BY_USER):return
            dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,message_type=Gtk.MessageType.ERROR,
                                     buttons=Gtk.ButtonsType.CLOSE,text='The export could not be saved')
            dialog.format_secondary_text(error.message);dialog.run();dialog.destroy()

        def do_shutdown(self):
            self.closing.set()
            if self.backend_thread is not None:self.backend_thread.join(timeout=3)
            self.backend.stop()
            Gtk.Application.do_shutdown(self)

    return SplatStudio()


def main():
    parser=argparse.ArgumentParser(description='Splat Studio desktop application')
    parser.add_argument('--data',type=Path,default=ROOT/'data');parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    application=create_application(args.data,args.port)
    from gi.repository import GLib
    for number in (signal.SIGINT,signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,number,lambda:application.quit())
    raise SystemExit(application.run([str(ROOT/'desktop.py')]))

if __name__=='__main__':main()
