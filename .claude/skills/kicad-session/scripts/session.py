#!/usr/bin/env python3
"""Open and close KiCad 10 for an agent working alone, on Linux.

The Linux twin of session.ps1 (Windows): same commands, same JSON, same exit codes.

  session.py open   --board x.kicad_pcb | --project x.kicad_pro
  session.py close  --board ... | --project ...  [--force]
  session.py status [--board ... | --project ...]

KiCad is launched with GDK_BACKEND=x11, so on a Wayland desktop it runs under XWayland and its
windows can be read and closed through X11 (DISPLAY must be set). Only the Python standard
library and libX11 are used.

Exit codes: 0 done; 3 refused before doing anything; 4 blocked (unsaved changes, a dialog, an
early exit or a timeout; KiCad is left as it is); 1 unexpected error.
"""
import argparse
import ctypes
import ctypes.util
import glob
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback

EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_BLOCKED = 0, 1, 3, 4
KICAD_NAMES = ('kicad', 'pcbnew', 'eeschema')


class Done(Exception):
    def __init__(self, body, code):
        super().__init__(body.get('message', ''))
        self.body, self.code = body, code


def result(body, code):
    raise Done(body, code)


def refuse(reason, message, **extra):
    result({'status': 'refused', 'reason': reason, **extra, 'message': message}, EXIT_REFUSED)


# ---------------------------------------------------------------------------- X11

class XClientMessageData(ctypes.Union):
    _fields_ = [('b', ctypes.c_char * 20), ('s', ctypes.c_short * 10), ('l', ctypes.c_long * 5)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong),
                ('message_type', ctypes.c_ulong), ('format', ctypes.c_int),
                ('data', XClientMessageData)]


class XEvent(ctypes.Union):
    _fields_ = [('type', ctypes.c_int), ('xclient', XClientMessageEvent), ('pad', ctypes.c_long * 24)]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int),
                ('height', ctypes.c_int), ('border_width', ctypes.c_int), ('depth', ctypes.c_int),
                ('visual', ctypes.c_void_p), ('root', ctypes.c_ulong), ('class_', ctypes.c_int),
                ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
                ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int),
                ('colormap', ctypes.c_ulong), ('map_installed', ctypes.c_int),
                ('map_state', ctypes.c_int), ('all_event_masks', ctypes.c_long),
                ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]


IS_VIEWABLE = 2
CLIENT_MESSAGE = 33
_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class X11:
    """The few Xlib calls needed: list a process's windows, read titles, ask one to close."""

    def __init__(self):
        name = ctypes.util.find_library('X11')
        if not name:
            raise RuntimeError('libX11 not found')
        x = ctypes.cdll.LoadLibrary(name)
        vp, ul, c_int = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
        x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
        x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
        x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, c_int]
        x.XGetWindowProperty.restype = c_int
        x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, c_int, ul,
                                         ctypes.POINTER(ul), ctypes.POINTER(c_int),
                                         ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(vp)]
        x.XQueryTree.restype = c_int
        x.XQueryTree.argtypes = [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(ul),
                                 ctypes.POINTER(ctypes.POINTER(ul)), ctypes.POINTER(ctypes.c_uint)]
        x.XGetWindowAttributes.restype = c_int
        x.XGetWindowAttributes.argtypes = [vp, ul, ctypes.POINTER(XWindowAttributes)]
        x.XSendEvent.restype = c_int
        x.XSendEvent.argtypes = [vp, ul, c_int, ctypes.c_long, ctypes.POINTER(XEvent)]
        x.XFlush.argtypes = [vp]
        x.XSync.argtypes = [vp, c_int]
        x.XFree.argtypes = [vp]
        x.XCloseDisplay.argtypes = [vp]
        x.XSetErrorHandler.restype, x.XSetErrorHandler.argtypes = vp, [_ERROR_HANDLER]
        # A window can vanish between listing and reading it; Xlib's default handler would
        # then exit the process. Ignore such errors: the read just comes back empty.
        self._handler = _ERROR_HANDLER(lambda display, event: 0)
        x.XSetErrorHandler(self._handler)
        self.x = x
        self.d = x.XOpenDisplay(None)
        if not self.d:
            raise RuntimeError('cannot open the X display (DISPLAY=%r)' % os.environ.get('DISPLAY'))
        self.root = x.XDefaultRootWindow(self.d)
        self._atoms = {}

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.x.XInternAtom(self.d, name.encode(), 0)
        return self._atoms[name]

    def prop(self, window, name, kind=0):
        """(format, data): bytes for format 8, a list of ints for format 32; None if absent."""
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        nitems, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self.x.XGetWindowProperty(self.d, window, self.atom(name), 0, 1 << 16, 0, kind,
                                           ctypes.byref(actual_type), ctypes.byref(actual_format),
                                           ctypes.byref(nitems), ctypes.byref(after),
                                           ctypes.byref(data))
        if status != 0 or not data.value or actual_type.value == 0:
            if data.value:
                self.x.XFree(data)
            return None
        try:
            n, fmt = nitems.value, actual_format.value
            if fmt == 8:
                return fmt, ctypes.string_at(data.value, n)
            if fmt == 32:  # Xlib hands 32-bit items back as C longs
                return fmt, list((ctypes.c_ulong * n).from_address(data.value))
            return fmt, None
        finally:
            self.x.XFree(data)

    def ints(self, window, name):
        p = self.prop(window, name)
        return p[1] if p and p[0] == 32 else []

    def title(self, window):
        p = self.prop(window, '_NET_WM_NAME', self.atom('UTF8_STRING'))
        if p and p[0] == 8:
            return p[1].decode('utf-8', 'replace')
        p = self.prop(window, 'WM_NAME')
        return p[1].decode('latin-1', 'replace') if p and p[0] == 8 else ''

    def children(self, window):
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids, count = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if not self.x.XQueryTree(self.d, window, ctypes.byref(root), ctypes.byref(parent),
                                 ctypes.byref(kids), ctypes.byref(count)):
            return []
        try:
            return [kids[i] for i in range(count.value)]
        finally:
            if kids:
                self.x.XFree(kids)

    def viewable(self, window):
        attrs = XWindowAttributes()
        if not self.x.XGetWindowAttributes(self.d, window, ctypes.byref(attrs)):
            return False
        return attrs.map_state == IS_VIEWABLE and not attrs.override_redirect

    def clients(self):
        """Top-level application windows: the window manager's list when there is one, else
        the root's children and, under a reparenting manager, their children."""
        listed = self.ints(self.root, '_NET_CLIENT_LIST')
        if listed:
            return listed
        found = []
        for child in self.children(self.root):
            if self.ints(child, '_NET_WM_PID'):
                found.append(child)
            else:
                found.extend(c for c in self.children(child) if self.ints(c, '_NET_WM_PID'))
        return found

    def windows_of(self, pid):
        dialog_type = self.atom('_NET_WM_WINDOW_TYPE_DIALOG')
        out = []
        for w in self.clients():
            if self.ints(w, '_NET_WM_PID')[:1] != [pid] or not self.viewable(w):
                continue
            transient = bool(self.ints(w, 'WM_TRANSIENT_FOR'))
            is_dialog = transient or dialog_type in self.ints(w, '_NET_WM_WINDOW_TYPE')
            out.append({'handle': int(w), 'title': self.title(w), 'dialog': is_dialog})
        return out

    def close(self, window):
        """Ask the window to close, as a window manager's close button does."""
        event = XEvent()
        event.xclient.type = CLIENT_MESSAGE
        event.xclient.window = window
        event.xclient.message_type = self.atom('WM_PROTOCOLS')
        event.xclient.format = 32
        event.xclient.data.l[0] = self.atom('WM_DELETE_WINDOW')
        event.xclient.data.l[1] = 0  # CurrentTime
        self.x.XSendEvent(self.d, window, 0, 0, ctypes.byref(event))
        self.x.XSync(self.d, 0)


_X = None


def xdisplay():
    global _X
    if _X is None:
        _X = X11()
    return _X


# ---------------------------------------------------------------------------- processes

def proc_info(pid):
    try:
        with open('/proc/%d/cmdline' % pid, 'rb') as f:
            argv = [a.decode('utf-8', 'replace') for a in f.read().split(b'\0') if a]
        with open('/proc/%d/stat' % pid) as f:
            fields = f.read().rsplit(')', 1)[1].split()
        try:
            exe = os.readlink('/proc/%d/exe' % pid)
        except OSError:
            exe = None
        with open('/proc/%d/comm' % pid) as f:
            name = f.read().strip()
    except (OSError, IndexError):
        return None
    if fields[0] == 'Z':
        return None
    return {'pid': pid, 'name': name, 'path': exe, 'argv': argv,
            'cpu_ticks': int(fields[11]) + int(fields[12])}


def kicad_processes():
    out = []
    for entry in os.listdir('/proc'):
        if entry.isdigit():
            info = proc_info(int(entry))
            if info and info['name'] in KICAD_NAMES:
                out.append(info)
    return out


def alive(pid):
    return proc_info(pid) is not None


def wait_gone(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


# ---------------------------------------------------------------------------- target

def find_exe(kicad_bin, name):
    if kicad_bin:
        path = os.path.join(kicad_bin, name)
        return os.path.realpath(path) if os.path.exists(path) else path
    found = shutil.which(name)
    return os.path.realpath(found) if found else name


def resolve_target(args):
    if args.board and args.project:
        refuse('board_and_project', 'Pass --board or --project, not both.')
    if not args.board and not args.project:
        refuse('missing_target', 'Pass --board <x.kicad_pcb> or --project <x.kicad_pro>.')
    given = args.project or args.board
    full = os.path.abspath(os.path.expanduser(given))
    want = '.kicad_pro' if args.project else '.kicad_pcb'
    if os.path.splitext(full)[1] != want:
        refuse('wrong_file_type', 'Expected a %s file.' % want, path=full)
    if not os.path.isfile(full):
        refuse('file_not_found', 'No such file.', path=full)
    stem = os.path.splitext(full)[0]
    board, sch = stem + '.kicad_pcb', stem + '.kicad_sch'
    if args.project:
        missing = [p for p in (board, sch) if not os.path.isfile(p)]
        if missing:
            refuse('project_files_missing',
                   "Project mode opens the project's schematic and board; these are missing.",
                   missing=missing)
    return {
        'mode': 'project' if args.project else 'board',
        'path': full, 'board': board, 'schematic': sch if args.project else None,
        'project': stem + '.kicad_pro', 'base': os.path.basename(stem),
        'dir': os.path.dirname(full),
        'exe': find_exe(args.kicad_bin, 'kicad' if args.project else 'pcbnew'),
        # Frames to wait for: the PCB editor; or the project manager and both editors.
        'frames_expected': 3 if args.project else 1,
    }


def owned(info, target):
    """The process is the one this script launched for this target: the expected KiCad
    executable, with the target's path among its arguments."""
    return bool(info and info['path'] and os.path.realpath(info['path']) == target['exe']
                and target['path'] in info['argv'])


def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'kicad-session')


def state_path(target):
    digest = hashlib.sha256(target['path'].encode()).hexdigest()[:16]
    return os.path.join(state_dir(), digest + '.json')


def read_session(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_session(path, session):
    os.makedirs(state_dir(), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(session, f, indent=2)


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------- windows

def window_state(pid, target):
    """Frames (no owner) whose title names the project or board, and dialogs (transient
    windows: a question KiCad is asking, or a progress window). '*' before an editor's title
    marks unsaved changes. GTK draws a dialog's text itself, so only its title is readable."""
    frames, other_frames, dialogs = [], [], []
    for w in xdisplay().windows_of(pid):
        if w['dialog']:
            dialogs.append({'title': w['title'], 'text': None})
        elif target['base'].lower() in w['title'].lower():
            frames.append({'handle': w['handle'], 'title': w['title']})
        elif w['title'].strip():
            other_frames.append(w['title'])
    dirty = [f['title'] for f in frames if f['title'].startswith('*')]
    return {'frames': frames, 'frame_titles': [f['title'] for f in frames], 'dirty': bool(dirty),
            'dirty_frames': dirty, 'dialogs': dialogs, 'other_frames': other_frames}


def close_window(windows, target, session):
    """The PCB editor in board mode; the project manager in project mode (closing it closes
    both editors and keeps them listed as open for the next launch)."""
    if target['mode'] == 'board':
        return windows['frames'][0] if windows['frames'] else None
    handle = (session or {}).get('manager_handle')
    for f in windows['frames']:
        if handle and f['handle'] == handle:
            return f
    # The project manager's title is '<project> — KiCad <version>' in every UI language.
    return next((f for f in windows['frames'] if re.search(r'KiCad \d', f['title'])), None)


def api_sockets():
    """KiCad's IPC sockets that accept a connection: $TMPDIR/kicad/api*.sock."""
    base = os.path.join(os.environ.get('TMPDIR') or '/tmp', 'kicad')
    live = []
    for path in sorted(glob.glob(os.path.join(base, 'api*.sock'))):
        try:
            if not stat.S_ISSOCK(os.stat(path).st_mode):
                continue
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(path)
            live.append(path)
        except OSError:
            pass
    return live


# ---------------------------------------------------------------------------- locks, settings

def locks(target):
    """KiCad's lock files for the target: '~<file>.lck', JSON naming user and host. Board
    mode: the board and its project. Project mode: also every schematic sheet beside them."""
    names = [os.path.basename(target['board']), os.path.basename(target['project'])]
    if target['mode'] == 'project':
        names += sorted(n for n in os.listdir(target['dir']) if n.endswith('.kicad_sch'))
    out = []
    for name in dict.fromkeys(names):
        path = os.path.join(target['dir'], '~%s.lck' % name)
        if os.path.exists(path):
            owner = {}
            try:
                with open(path) as f:
                    owner = json.load(f)
            except (OSError, ValueError):
                pass
            out.append({'path': path, 'username': owner.get('username'),
                        'hostname': owner.get('hostname')})
    return out


def me():
    host = socket.gethostname()
    return pwd.getpwuid(os.getuid()).pw_name, {host.lower(), host.split('.')[0].lower()}


def remove_own_stale_locks(target):
    """Called only once the process this script launched has exited and no KiCad runs at all,
    so no live KiCad can own these locks. Only locks naming this user on this machine go."""
    user, hosts = me()
    removed = []
    for lock in locks(target):
        if lock['username'] == user and (lock['hostname'] or '').lower() in hosts:
            remove(lock['path'])
            removed.append(lock['path'])
    return removed


def common_settings():
    """The settings this launch will use: KICAD_CONFIG_HOME/10.0 when set, as KiCad does."""
    if os.environ.get('KICAD_CONFIG_HOME'):
        base = os.path.join(os.environ['KICAD_CONFIG_HOME'], '10.0')
    else:
        config = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
        base = os.path.join(config, 'kicad', '10.0')
    path = os.path.join(base, 'kicad_common.json')
    try:
        with open(path) as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    return {'file': path,
            'api_server': bool((j.get('api') or {}).get('enable_server')),
            'remember_open_files': bool((j.get('session') or {}).get('remember_open_files'))}


def set_restore_entries(target):
    """Session restore reopens the files .kicad_prl lists as open (KICAD_MANAGER_FRAME::OnIdle
    reads only each entry's name and 'open'). Marks the schematic and board open."""
    prl = os.path.splitext(target['project'])[0] + '.kicad_prl'
    wanted = [os.path.basename(target['schematic']), os.path.basename(target['board'])]
    try:
        with open(prl) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {'meta': {'filename': os.path.basename(prl), 'version': 5}}
    if not isinstance(data.get('project'), dict):
        data['project'] = {}
    files = data['project'].get('files') or []
    changed = []
    for name in wanted:
        entry = next((e for e in files if isinstance(e, dict) and e.get('name') == name), None)
        if entry is None:
            files.append({'name': name, 'open': True})
            changed.append(name)
        elif entry.get('open') is not True:
            entry['open'] = True
            changed.append(name)
    if changed:
        data['project']['files'] = files
        with open(prl, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
    return changed


# ---------------------------------------------------------------------------- commands

def cmd_open(args):
    target = resolve_target(args)
    spath = state_path(target)
    existing = read_session(spath)
    if existing:
        info = proc_info(existing['pid'])
        if owned(info, target):
            w = window_state(existing['pid'], target)
            ready = len(w['frames']) >= target['frames_expected'] and not w['dialogs']
            result({'status': 'open' if ready else 'blocked',
                    'reason': None if ready else ('dialog_open' if w['dialogs'] else 'not_ready'),
                    'reused': True, 'mode': target['mode'], 'pid': existing['pid'],
                    'target': target['path'], 'windows': w['frame_titles'],
                    'unsaved': w['dirty_frames'], 'dialogs': w['dialogs'],
                    'message': 'Already open in a KiCad this script launched.' if ready else
                    'The KiCad this script launched is not ready; see dialogs. '
                    '`close --force` ends it.'}, EXIT_OK if ready else EXIT_BLOCKED)
        remove(spath)

    running = kicad_processes()
    if running and not args.allow_other_kicad:
        refuse('other_kicad_running',
               'Another KiCad is running. The MCP server would see two IPC endpoints. Ask the '
               'user to close it; never close a KiCad this script did not open.',
               target=target['path'],
               running=[{'pid': p['pid'], 'name': p['name'],
                         'windows': [w['title'] for w in xdisplay().windows_of(p['pid'])]}
                        for p in running])
    if not os.path.isfile(target['exe']):
        refuse('kicad_not_found', 'KiCad executable not found; pass --kicad-bin.', exe=target['exe'])
    xdisplay()  # fail now, before launching, if there is no X display

    settings = common_settings()
    if settings and not settings['api_server']:
        refuse('api_server_off', "KiCad's IPC API server is off, so the MCP server could not "
               'reach KiCad. Ask the user to enable it: Preferences > Plugins > Enable KiCad API.',
               settings=settings['file'])
    marked = None
    if target['mode'] == 'project':
        if settings and not settings['remember_open_files']:
            refuse('session_restore_off', 'Project mode needs KiCad to reopen the editors itself. '
                   'Ask the user to enable Preferences > Common > "Remember open files for next '
                   'project launch", or use --board to open the PCB editor alone.',
                   settings=settings['file'])
        marked = set_restore_entries(target)

    locks_before = [l['path'] for l in locks(target)]
    timeout = args.timeout_seconds or (120 if target['mode'] == 'project' else 90)
    started = time.monotonic()
    os.makedirs(state_dir(), exist_ok=True)
    log = open(os.path.join(state_dir(), 'kicad-%s.log' % os.path.basename(spath)[:-5]), 'ab')
    env = dict(os.environ, GDK_BACKEND='x11')
    child = subprocess.Popen([target['exe'], target['path']], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, env=env, start_new_session=True,
                             cwd=target['dir'])
    session = {'pid': child.pid, 'mode': target['mode'], 'target': target['path'],
               'exe': target['exe'], 'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
               'manager_handle': None}
    write_session(spath, session)

    def elapsed_ms():
        return int((time.monotonic() - started) * 1000)

    ready_polls, idle_since, last_cpu, last_cpu_at = 0, None, 0, 0.0
    tick = os.sysconf('SC_CLK_TCK')
    while time.monotonic() - started < timeout:
        if child.poll() is not None:
            remove(spath)
            result({'status': 'failed', 'reason': 'exited_during_startup', 'mode': target['mode'],
                    'target': target['path'], 'pid': child.pid, 'exit_code': child.returncode,
                    'elapsed_ms': elapsed_ms(), 'log': log.name,
                    'message': 'KiCad exited before its windows came up. Retry once; if it exits '
                    'again, tell the user.'}, EXIT_BLOCKED)
        w = window_state(child.pid, target)

        # The project manager is the first frame to appear; remember it for close.
        if target['mode'] == 'project' and not session['manager_handle'] and w['frames']:
            manager = close_window(w, target, None)
            session['manager_handle'] = (manager or w['frames'][0])['handle']
            write_session(spath, session)

        sockets = api_sockets()
        if len(w['frames']) >= target['frames_expected'] and not w['dialogs'] and sockets:
            ready_polls += 1
            if ready_polls >= 3:
                result({'status': 'open', 'reused': False, 'mode': target['mode'],
                        'pid': child.pid, 'target': target['path'], 'windows': w['frame_titles'],
                        'unsaved': w['dirty_frames'], 'ipc_sockets': sockets,
                        'elapsed_ms': elapsed_ms(), 'marked_open_in_prl': marked,
                        'stale_locks_before': locks_before,
                        'message': "Open. Check with the MCP server's check_kicad_ui (or "
                        'get_editor_state in board mode) before editing.'}, EXIT_OK)
        else:
            ready_polls = 0

        # A dialog is up and KiCad has been idle for 5 s: it is waiting for an answer, not
        # loading (a progress window keeps the process busy).
        now = time.monotonic() - started
        if now - last_cpu_at >= 2:
            info = proc_info(child.pid)
            cpu = info['cpu_ticks'] if info else last_cpu
            busy = (cpu - last_cpu) / tick > 0.1
            last_cpu, last_cpu_at = cpu, now
            if w['dialogs'] and not busy:
                idle_since = now if idle_since is None else idle_since
            else:
                idle_since = None
        if idle_since is not None and now - idle_since >= 5:
            result({'status': 'blocked', 'reason': 'dialog_open', 'mode': target['mode'],
                    'target': target['path'], 'pid': child.pid, 'windows': w['frame_titles'],
                    'dialogs': w['dialogs'], 'elapsed_ms': elapsed_ms(),
                    'message': 'KiCad is asking a question. Nothing has been edited yet, so '
                    '`close --force` is safe; tell the user what it asks (its title), and do '
                    'not answer it for them.'}, EXIT_BLOCKED)
        time.sleep(0.5)

    w = window_state(child.pid, target)
    result({'status': 'blocked', 'reason': 'timeout', 'mode': target['mode'],
            'target': target['path'], 'pid': child.pid, 'windows': w['frame_titles'],
            'dialogs': w['dialogs'], 'ipc_sockets': api_sockets(), 'elapsed_ms': elapsed_ms(),
            'message': 'Not ready after %d s. KiCad is left running; `close --force` ends it.'
            % timeout}, EXIT_BLOCKED)


def cmd_close(args):
    target = resolve_target(args)
    spath = state_path(target)
    session = read_session(spath)
    if not session:
        refuse('not_opened_here', 'This script did not open this, so it does not close it. A '
               'KiCad the user opened is theirs to close.', target=target['path'],
               running=[{'pid': p['pid'], 'name': p['name']} for p in kicad_processes()])
    pid = session['pid']
    info = proc_info(pid)
    if not info:
        remove(spath)
        removed = [] if kicad_processes() else remove_own_stale_locks(target)
        result({'status': 'closed', 'already_exited': True, 'mode': target['mode'],
                'target': target['path'], 'pid': pid, 'removed_stale_locks': removed,
                'locks_left': [l['path'] for l in locks(target)],
                'message': 'KiCad had already exited.'}, EXIT_OK)
    if not owned(info, target):
        remove(spath)
        refuse('process_not_ours', 'The recorded process is no longer the KiCad this script '
               'opened; nothing was closed.', target=target['path'], pid=pid,
               command_line=info['argv'])

    w = window_state(pid, target)
    window = close_window(w, target, session)
    timeout = args.timeout_seconds or 30
    started = time.monotonic()

    if not args.force and (w['dirty'] or w['dialogs'] or not window):
        reason = ('unsaved_changes' if w['dirty'] else 'dialog_open' if w['dialogs']
                  else 'window_not_found')
        message = {
            'unsaved_changes': "Nothing was closed. Save the board with the MCP server's "
            'save_project and close again. KiCad 10 cannot save a schematic over IPC: unsaved '
            'schematic changes need the user, or --force to discard them.',
            'dialog_open': 'Nothing was closed. KiCad is showing a dialog; tell the user what it '
            'asks. --force ends KiCad and loses unsaved changes.',
            'window_not_found': 'Nothing was closed: the window to close was not found. --force '
            'ends KiCad and loses unsaved changes.'}[reason]
        result({'status': 'blocked', 'reason': reason, 'mode': target['mode'],
                'target': target['path'], 'pid': pid, 'windows': w['frame_titles'],
                'unsaved': w['dirty_frames'], 'dialogs': w['dialogs'], 'message': message},
               EXIT_BLOCKED)

    tried = False
    if not w['dirty'] and not w['dialogs'] and window:
        tried = True
        xdisplay().close(window['handle'])
        if wait_gone(pid, timeout):
            remove(spath)
            result({'status': 'closed', 'forced': False, 'mode': target['mode'],
                    'target': target['path'], 'pid': pid,
                    'elapsed_ms': int((time.monotonic() - started) * 1000),
                    'locks_left': [l['path'] for l in locks(target)],
                    'message': 'Closed the way a user would.'}, EXIT_OK)
        if not args.force:
            after = window_state(pid, target)
            result({'status': 'blocked', 'reason': 'close_did_not_finish', 'mode': target['mode'],
                    'target': target['path'], 'pid': pid, 'windows': after['frame_titles'],
                    'dialogs': after['dialogs'],
                    'elapsed_ms': int((time.monotonic() - started) * 1000),
                    'message': 'KiCad did not exit. Tell the user what the dialog asks; --force '
                    'ends KiCad and loses unsaved changes.'}, EXIT_BLOCKED)

    # --force: end the process. Unsaved changes are lost; KiCad leaves its locks behind.
    before = window_state(pid, target)
    os.kill(pid, signal.SIGKILL)
    wait_gone(pid, 10)
    remove(spath)
    others = kicad_processes()
    removed = [] if others else remove_own_stale_locks(target)
    result({'status': 'closed', 'forced': True, 'mode': target['mode'], 'target': target['path'],
            'pid': pid, 'graceful_close_tried': tried, 'unsaved_discarded': before['dirty_frames'],
            'dialogs_dismissed': before['dialogs'], 'removed_stale_locks': removed,
            'locks_left': [l['path'] for l in locks(target)],
            'elapsed_ms': int((time.monotonic() - started) * 1000),
            'message': 'Ended KiCad. Another KiCad is running, so the locks were left in place.'
            if others else 'Ended KiCad and removed the locks it left.'}, EXIT_OK)


def cmd_status(args):
    target = resolve_target(args) if (args.board or args.project) else None
    sessions = []
    if os.path.isdir(state_dir()):
        for name in sorted(os.listdir(state_dir())):
            if name.endswith('.json'):
                s = read_session(os.path.join(state_dir(), name))
                if s:
                    info = proc_info(s['pid'])
                    sessions.append({'pid': s['pid'], 'mode': s.get('mode'),
                                     'target': s.get('target'), 'started': s.get('started'),
                                     'running': bool(info and s.get('target') in info['argv'])})
    try:
        x = xdisplay()
        windows = lambda pid: ['%s [%s]' % (w['title'], 'dialog' if w['dialog'] else 'frame')
                               for w in x.windows_of(pid)]
        display_error = None
    except RuntimeError as e:
        windows, display_error = (lambda pid: None), str(e)
    result({'status': 'ok', 'display': os.environ.get('DISPLAY'), 'display_error': display_error,
            'kicad_processes': [{'pid': p['pid'], 'name': p['name'], 'command_line': p['argv'],
                                 'windows': windows(p['pid'])} for p in kicad_processes()],
            'sessions': sessions, 'locks': locks(target) if target else None,
            'ipc_sockets': api_sockets(), 'settings': common_settings()}, EXIT_OK)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['open', 'close', 'status'])
    parser.add_argument('--board')
    parser.add_argument('--project')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--timeout-seconds', type=int, default=0)
    parser.add_argument('--kicad-bin', help='folder holding kicad and pcbnew (default: PATH)')
    parser.add_argument('--allow-other-kicad', action='store_true')
    args = parser.parse_args()
    try:
        {'open': cmd_open, 'close': cmd_close, 'status': cmd_status}[args.action](args)
    except Done as done:
        print(json.dumps(done.body, indent=2, ensure_ascii=False))
        return done.code
    except Exception as e:  # noqa: BLE001 - report every failure as JSON
        print(json.dumps({'status': 'error', 'message': str(e),
                          'at': traceback.format_exc(limit=3)}, indent=2))
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == '__main__':
    sys.exit(main())
