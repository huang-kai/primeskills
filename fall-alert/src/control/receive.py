import os, signal, socket, subprocess as sp, sys, time, select, json
from pathlib import Path
from watch_display import UI_OWNED, restore_ui
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst
D=Path(__file__).parent
Gst.init(None)
def log(*args):print(*args,flush=True)
def cmd(*args,timeout=10):
    return sp.run(args,text=True,capture_output=True,timeout=timeout)
def checked(*args):
    r=cmd(*args);
    if r.returncode:raise RuntimeError(' '.join(args[:4])+' '+r.stderr[-350:])
def onexit(sig,frame):raise SystemExit(0)
signal.signal(signal.SIGTERM,onexit)
width,height=480,272
managed='--managed' in sys.argv
case_once=os.environ.get('T1_FALL_CASE') == '1'
session_seconds=480 if '--confirm-family' in sys.argv else 125
ui_marker=UI_OWNED
pipeline=Gst.Pipeline.new('single-stream')
src=Gst.ElementFactory.make('appsrc','frames')
q=Gst.ElementFactory.make('queue','latest')
screen=Gst.ElementFactory.make('kmssink','display')
count=None if case_once else Gst.ElementFactory.make('fpsdisplaysink','counter')
if any(element is None for element in (pipeline, src, q, screen)):
    raise RuntimeError("Missing GStreamer display elements")
src.props.caps=Gst.Caps.from_string('video/x-raw,format=BGRx,width=480,height=272,framerate=10/1')
src.props.is_live=True;src.props.format=Gst.Format.TIME;src.props.do_timestamp=True
q.props.leaky=2;q.props.max_size_buffers=1;q.props.max_size_bytes=0;q.props.max_size_time=0
screen.set_property('driver-name','rockchip');screen.set_property('connector-id',177)
screen.set_property('force-modesetting',True);screen.set_property('restore-crtc',True)
screen.props.sync=False
sink=screen
if not case_once:
    if count is None:
        raise RuntimeError("Missing fpsdisplaysink")
    count.props.sync=False;count.props.text_overlay=False;count.props.video_sink=screen
    count.props.fps_update_interval=500
    sink=count
for elem in (src,q,sink):pipeline.add(elem)
if not src.link(q) or not q.link(sink):
    raise RuntimeError("Cannot link display pipeline")
bus=pipeline.get_bus()
decoder=None; recovery='t1-preview-recover-'+str(os.getpid()); paused=False;armed=False
submitted=0;tfirst=None;tlast=None;framesize=width*height*4;rendered=0;dropped=0
def observe():
    global rendered,dropped
    rendered=count.get_property('frames-rendered');dropped=count.get_property('frames-dropped')
def errcheck():
    msg=bus.pop_filtered(Gst.MessageType.ERROR)
    if msg:
        err,details=msg.parse_error();raise RuntimeError(str(err)+' '+str(details)[:220])
try:
    if cmd('systemctl', 'is-active', 'display_ui.service').stdout.strip() != 'active':
        raise RuntimeError('Factory display is unavailable')
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('10.1.1.100',38473));listener.listen(1);listener.settimeout(130)
        log('RECEIVER_READY once-only KMS after first decoded frame')
        conn,peer=listener.accept()
        if peer[0]!='10.1.1.101':conn.close();raise RuntimeError('unexpected source')
        with conn, open(sys.stderr.fileno(), "w", closefd=False) as ferr:
            decoder=sp.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','1','-flags','low_delay','-probesize','32768','-analyzeduration','0','-f','mpegts','-i','pipe:0','-map','0:v:0','-an','-vsync','0','-pix_fmt','bgra','-threads','1','-f','rawvideo','pipe:1'],stdin=conn,stdout=sp.PIPE,stderr=ferr,bufsize=0)
            fd=decoder.stdout.fileno();partial=bytearray();start=time.monotonic();lastdata=start
            while (managed or time.monotonic()-start<session_seconds+3) and not (D/'stop').exists():
                errcheck()
                if not select.select([fd],[],[],.2)[0]:
                    if time.monotonic()-lastdata>(3 if tfirst else 15):break
                    continue
                b=os.read(fd,min(262144,framesize-len(partial)))
                if not b:break
                partial.extend(b);lastdata=time.monotonic()
                if len(partial)<framesize:continue
                if not paused:
                    if managed:
                        ui_marker.touch()
                    else:
                        checked('sudo','-n','systemd-run','--quiet','--unit='+recovery,'--on-active='+str(session_seconds+25)+'s','--timer-property=AccuracySec=1s','/usr/bin/python3','-B',str(D/'watch_display.py'),'--restore-force')
                        armed=True
                    paused=True
                    checked('sudo','-n','systemctl','stop','display_ui.service')
                    if pipeline.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:errcheck();raise RuntimeError('KMS failed to start')
                    tfirst=time.monotonic();log('ONE_SCREEN_SESSION')
                buf=Gst.Buffer.new_allocate(None,framesize,None);buf.fill(0,bytes(partial));partial.clear()
                if src.emit('push-buffer',buf)!=Gst.FlowReturn.OK:raise RuntimeError('display push failed')
                if not case_once:
                    submitted+=1;tlast=time.monotonic()
                    if submitted in (1,3,10) or submitted%25==0:
                        observe();log('VIDEO',submitted,'rendered',rendered,'dropped',dropped)
            if paused and not case_once:
                time.sleep(.12);errcheck();observe()
finally:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pipeline.set_state(Gst.State.NULL);pipeline.get_state(2*Gst.SECOND)
    if decoder is not None:
        if decoder.poll() is None:decoder.terminate()
        try:decoder.wait(timeout=2)
        except sp.TimeoutExpired:decoder.kill();decoder.wait()
        decoder.stdout.close()
    if paused:
        try:
            restore_ui(force=True)
        except Exception as error:
            log('UI_RESTORE_ERROR',type(error).__name__)
        status=cmd('systemctl','is-active','display_ui.service').stdout.strip()
        log('UI_RESTORE',status)
        if status=='active' and managed:ui_marker.unlink(missing_ok=True)
        if status=='active' and armed:cmd('sudo','-n','systemctl','stop',recovery+'.timer')
    if not case_once:
        log('SUMMARY',json.dumps(dict(submitted=submitted,rendered=rendered,dropped=dropped,session_s=round(tlast-tfirst,2) if tlast and tfirst else 0)))
