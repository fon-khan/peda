#
#       PEDA - Python Exploit Development Assistance for GDB (python3 version)
#
#       Copyright (C) 2012 Long Le Dinh <longld at vnsecurity.net>
#       Copyright (C) 2014 Jeffrey Crowell <crowell at bu.edu>
#
#       License: see LICENSE file for details
#
from __future__ import print_function
import gdb
import tempfile
import pprint
import inspect
import sys
import struct
import string
import re
import itertools
import functools
from subprocess import *
import binascii
import config

from codecs import encode, decode

try:    from StringIO import StringIO # Python2
except: from io       import StringIO # Python3

try:    unicode
except: unicode = str

try: input = raw_input
except: pass

# http://wiki.python.org/moin/PythonDecoratorLibrary#Memoize
# http://stackoverflow.com/questions/8856164/class-decorator-decorating-method-in-python
class memoized(object):
    """
    Decorator. Caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned
    (not reevaluated).
    """
    def __init__(self, func):
        self.func = func
        self.instance = None # bind with instance class of decorated method
        self.cache = {}
        self.__doc__ = inspect.getdoc(self.func)

    def __call__(self, *args, **kwargs):
        try:
            return self.cache[(self.func, self.instance, args) + tuple(kwargs.items())]
        except KeyError:
            if self.instance is None:
                value = self.func(*args, **kwargs)
            else:
                value = self.func(self.instance, *args, **kwargs)
            self.cache[(self.func, self.instance, args) + tuple(kwargs.items())] = value
            return value
        except TypeError:
            # uncachable -- for instance, passing a list as an argument.
            # Better to not cache than to blow up entirely.
            if self.instance is None:
                return self.func(*args, **kwargs)
            else:
                return self.func(self.instance, *args, **kwargs)

    def __repr__(self):
        """Return the function's docstring."""
        return self.__doc__

    def __get__(self, obj, objtype):
        """Support instance methods."""
        if obj is None:
            return self
        else:
            self.instance = obj
            return self

    def _reset(self):
        """Reset the cache"""
        for cached in list(self.cache.keys()):
            if cached[0] == self.func and cached[1] == self.instance:
                del self.cache[cached]

def reset_cache(module=None):
    """
    Reset memoized caches of an instance/module
    """
    if module is None:
        module = sys.modules['__main__']

    for m in dir(module):
        m = getattr(module, m)
        if isinstance(m, memoized):
            m._reset()
        else:
            for f in dir(m):
                f = getattr(m, f)
                if isinstance(f, memoized):
                    f._reset()

    return True

def tmpfile(pref="peda-"):
    """Create and return a temporary file with custom prefix"""
    return tempfile.NamedTemporaryFile(prefix=pref)

def colorize(text, color=None, attrib=None):
    """
    Colorize text using ansicolor
    ref: https://github.com/hellman/libcolors/blob/master/libcolors.py
    """
    # ansicolor definitions
    COLORS = {"black": "30", "red": "31", "green": "32", "yellow": "33",
                "blue": "34", "purple": "35", "cyan": "36", "white": "37"}
    CATTRS = {"regular": "0", "bold": "1", "underline": "4", "strike": "9",
                "light": "1", "dark": "2", "invert": "7"}

    CPRE = '\033['
    CSUF = '\033[0m'

    if not config.Option.get("ansicolor"):
        return text

    ccode = ""
    if attrib:
        for attr in attrib.lower().split():
            attr = attr.strip(",+|")
            if attr in CATTRS:
                ccode += ";" + CATTRS[attr]
    if color in COLORS:
        ccode += ";" + COLORS[color]
    return CPRE + ccode + "m" + text + CSUF

def green(text, attrib=None):
    """Wrapper for colorize(text, 'green')"""
    return colorize(text, "green", attrib)

def red(text, attrib=None):
    """Wrapper for colorize(text, 'red')"""
    return colorize(text, "red", attrib)

def yellow(text, attrib=None):
    """Wrapper for colorize(text, 'yellow')"""
    return colorize(text, "yellow", attrib)

def blue(text, attrib=None):
    """Wrapper for colorize(text, 'blue')"""
    return colorize(text, "blue", attrib)

class message(object):
    """
    Generic pretty printer with redirection.
    It also suports buffering using bufferize() and flush().
    """

    def __init__(self):
        self.out = sys.stdout
        self.buffering = 0

    def bufferize(self, f=None):
        """Activate message's bufferization, can also be used as a decorater."""
        return
        if f != None:
            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                self.bufferize()
                f(*args, **kwargs)
                self.flush()
            return wrapper

        # If we are still using stdio we need to change it.
        if not self.buffering:
            self.out = StringIO()
        self.buffering += 1

    def flush(self):
        if not self.buffering:
            raise ValueError("Tried to flush a message that is not bufferising.")
        self.buffering -= 1

        # We only need to flush if this is the lowest recursion level.
        if not self.buffering:
            self.out.flush()
            sys.stdout.write(self.out.getvalue())
            self.out = sys.stdout

    def __call__(self, text, color=None, attrib=None, teefd=None):
        if not teefd:
            teefd = config.Option.get("_teefd")

        if (isinstance(text, str) or isinstance(text, unicode)):
            text = text.replace("\x00", colorize(".", "red", None))
            print(colorize(text, color, attrib), file=self.out)
            if teefd:
                print(colorize(text, color, attrib), file=teefd)
        else:
            pprint.pprint(text, self.out)
            if teefd:
                pprint.pprint(text, teefd)

msg = message()

def warning_msg(text):
    """Colorize warning message with prefix"""
    msg(colorize("Warning: " + str(text), "yellow"))

def error_msg(text):
    """Colorize error message with prefix"""
    msg(colorize("Error: " + str(text), "red"))

def debug_msg(text, prefix="Debug"):
    """Colorize debug message with prefix"""
    if config.Option.get("debug"):
        msg(colorize("%s: %s" % (prefix, str(text)), "cyan"))

def trim(docstring):
    """
    Handle docstring indentation, ref: PEP257
    """
    if not docstring:
        return ''
    # Convert tabs to spaces (following the normal Python rules)
    # and split into a list of lines:
    lines = docstring.expandtabs().splitlines()
    # Determine minimum indentation (first line doesn't count):
    indent = sys.maxsize
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped:
            indent = min(indent, len(line) - len(stripped))
    # Remove indentation (first line is special):
    trimmed = [lines[0].strip()]
    if indent < sys.maxsize:
        for line in lines[1:]:
            trimmed.append(line[indent:].rstrip())
    # Strip off trailing and leading blank lines:
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
    # Return a single string:
    return '\n'.join(trimmed)

def separator(title = ''):
    import struct, termios, fcntl, sys
    try:
        _height, width = struct.unpack('hh', fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, '1234'))
    except:
        width = 80
    w = width - 2 - len(title)
    return '[%s%s%s]' % ('-' * (w // 2), title, '-' * ((w + 1) // 2))

def pager(text, pagesize=None):
    """
    Paging output, mimic external command less/more
    """
    if not pagesize:
        pagesize = config.Option.get("pagesize")

    if pagesize <= 0:
        msg(text)
        return

    i = 1
    text = text.splitlines()
    l = len(text)

    for line in text:
        msg(line)
        if i % pagesize == 0:
            ans = input("--More--(%d/%d)" % (i, l))
            if ans.lower().strip() == "q":
                break
        i += 1

    return

def execute_external_command(command, cmd_input=None):
    """
    Execute external command and capture its output

    Args:
        - command (String)

    Returns:
        - output of command (String)
    """
    result = ""
    P = Popen([command], stdout=PIPE, stdin=PIPE, stderr=PIPE, shell=True)
    (result, err) = P.communicate(cmd_input)
    if err and config.Option.get("debug"):
        warning_msg(err)

    return decode(result, 'utf-8')

def is_printable(text, printables=""):
    """
    Check if a string is printable
    """
    try:    text = text.decode('ascii')
    except: return False

    if all(c in string.printable for c in text):
        return True

    return False

def is_math_exp(str):
    """
    Check if a string is a math exprssion
    """
    charset = set("0123456789abcdefx+-*/%^")
    opers = set("+-*/%^")
    exp = set(str.lower())
    return (exp & opers != set()) and (exp - charset == set())

def normalize_argv(args, size=0):
    """
    Normalize argv to list with predefined length
    """
    args = list(args)
    for (idx, val) in enumerate(args):
        if to_int(val) is not None:
            args[idx] = to_int(val)
        if size and idx == size:
            return args[:idx]

    if size == 0:
        return args
    for i in range(len(args), size):
        args += [None]
    return args

def to_hexstr(str):
    """
    Convert a string to hex escape represent
    """
    return "".join(["\\x%02x" % ord(i) for i in str])

def to_hex(num):
    """
    Convert a number to hex format
    """
    if num < 0:
        return "-0x%x" % (-num)
    else:
        return "0x%x" % num

def to_address(num):
    """
    Convert a number to address format in hex
    """
    if num < 0:
        return to_hex(num)
    if num > 0xffffffff: # 64 bit
        return "0x%016x" % num
    else:
        return "0x%08x" % num

def to_int(val):
    """
    Convert a string to int number
    """
    try:    return int(str(val), 0)
    except: pass

    try:    return int(gdb.parse_and_eval(val))
    except: pass

    return None

def str2hex(str):
    """
    Convert a string to hex encoded format
    """
    result = binascii.hexlify(str)
    return result

def hex2str(hexnum, intsize=4):
    """
    Convert a number in hex format to string
    """

    if not isinstance(hexnum, str):
        nbits = intsize * 8
        hexnum = "0x%x" % ((hexnum + (1 << nbits)) % (1 << nbits))

    s = hexnum[2:]
    if len(s) % 2 != 0:
        s = "0" + s
    result=binascii.unhexlify(s)[::-1]
    return result

def int2hexstr(num, intsize=4):
    """
    Convert a number to hexified string
    """

    if intsize == 8:
        if num < 0:
            result = struct.pack("<q", num)
        else:
            result = struct.pack("<Q", num)
    else:
        if num < 0:
            result = struct.pack("<l", num)
        else:
            result = struct.pack("<L", num)
    return result

def list2hexstr(intlist, intsize=4):
    """
    Convert a list of number/string to hexified string
    """
    result = ""
    for value in intlist:
        if isinstance(value, str):
            result += value
        else:
            result += int2hexstr(value, intsize)
    return result

def str2intlist(data, intsize=4):
    """
    Convert a string to list of int
    """
    result = []
    data = decode(data,'string_escape')[::-1]
    l = len(data)
    data = ("\x00" * (intsize - l%intsize) + data) if l%intsize != 0 else data
    for i in range(0, l, intsize):
        if intsize == 8:
            val = struct.unpack(">Q", data[i:i+intsize])[0]
        else:
            val = struct.unpack(">L", data[i:i+intsize])[0]
        result = [val] + result
    return result

@memoized
def check_badchars(data, chars=None):
    """
    Check an address or a value if it contains badchars
    """
    if to_int(data) is None:
        to_search = data
    else:
        data = to_hex(to_int(data))[2:]
        if len(data) % 2 != 0:
            data = "0" + data
        to_search = decode(data,'hex')

    if not chars:
        chars = config.Option.get("badchars")

    if chars:
        for c in chars:
            if c in to_search:
                return True
    return False

@memoized
def format_address(addr, type):
    """Colorize an address"""
    colorcodes = {
        "data": "blue",
        "code": "red",
        "rodata": "green",
        "stack": "purple",
        "heap": "purple",
        "value": None
    }
    colorattr = {
        "data": None,
        "code": None,
        "rodata": None,
        "stack": None,
        "heap": "light",
        "value": None
    }

    return colorize(addr, colorcodes[type], colorattr[type])

@memoized
def format_reference_chain(chain):
    """
    Colorize a chain of references

    v = value
    t = type
    vn = value name (str)
    """
    v = t = vn = None
    text = ""

    if not chain:
        text += "Cannot access memory address"
    else:
        first = 1
        for (v, t, vn) in chain:
            if t != "value":
                text += "%s%s " % ("--> " if not first else "", format_address(v, t))
            else:
                text += "%s%s " % ("--> " if not first else "", v)
            first = 0

        if vn:
            text += "(%s)" % vn
        else:
            if v != "0x0":
                s = hex2str(v)
                if is_printable(s, "\x00"):
                    text += "(%s)" % s
    return text

def split_disasm_line(line):
    # example lines
    # '   0x41ea8a <main+10>:  sub    $0x128,%rsp'
    # '=> 0x8048560:\tmov    eax,ds:0x80499e0 ; hello world
    rprefix   = r'(.*?)'
    raddr     = r'(0x[0-9a-fA-F]+)'
    rname     = r'\s*(?:<(.*?)>)?'
    rinstr    = r':?\s*([^;]+)'
    rcomment  = r'(.*)'
    pattern = rprefix + raddr + rname + rinstr + rcomment

    # PANIC!
    p,a,n,i,c = re.match(pattern, line).groups()

    return p,int(a,16),n,i,(c or None)

# vulnerable C functions, source: rats/flawfinder
VULN_FUNCTIONS = [
    "exec", "system", "gets", "popen", "getenv", "strcpy", "strncpy", "strcat", "strncat",
    "memcpy", "bcopy", "printf", "sprintf", "snprintf", "scanf",  "getchar", "getc", "read",
    "recv", "tmp", "temp"
]

def format_disasm_code(code, nearby=None):
    """
    Format output of disassemble command with colors to highlight:
        - dangerous functions (rats/flawfinder)
        - branching: jmp, call, ret
        - testing: cmp, test

    Args:
        - code: input asm code (String)
        - nearby: address for nearby style format (Int)

    Returns:
        - colorized text code (String)
    """
    colorcodes = {
        "cmp": "red",
        "test": "red",
        "call": "green",
        "j": "yellow", # jump
        "ret": "blue",
    }
    result = ""

    if not code:
        return result

    if to_int(nearby) is not None:
        target = to_int(nearby)
    else:
        target = 0

    for line in code.splitlines():
        if ":" not in line or "Dump of assembler code" in line: # not an assembly line
            result += line + "\n"
        else:
            color = style = None

            prefix, addr, name, inst, comment = split_disasm_line(line)
            if not addr:
                result += line + "\n"
                return

            oaddr = re.search("\s*(0x[0-9a-fA-F]+)", line).group(1)

            opcode = inst.split(None, 1)[0]
            for c in colorcodes:
                if c not in opcode:
                    continue
                color = colorcodes[c]
                if c == "call":
                    if any(f in inst for f in VULN_FUNCTIONS):
                        style = "bold, underline"
                        color = "red"
                break

            if addr < target:
                style = "dark"
            elif addr == target:
                style = "bold"
                color = "green"

            code = colorize(inst, color, style)

            if name is not None:
                name = colorize(" <%s>" % name, color, "dark")
            else:
                name = ""

            if comment is not None:
                comment = colorize(";" + comment, color, "dark")
            else:
                comment = ""


            line = "%s%s%s:\t%s%s" % (prefix, oaddr, name, code, comment)
            result += line + "\n"

    return result.rstrip()

def cyclic_pattern_charset(charset_type=None):
    """
    Generate charset for cyclic pattern

    Args:
        - charset_type: charset type
            0: basic (0-9A-za-z)
            1: extended (default)
            2: maximum (almost printable chars)

    Returns:
        - list of charset
    """

    charset = []
    charset += ["ABCDEFGHIJKLMNOPQRSTUVWXYZ"] # string.uppercase
    charset += ["abcdefghijklmnopqrstuvwxyz"] # string.lowercase
    charset += ["0123456789"] # string.digits

    if not charset_type:
        charset_type = config.Option.get("pattern")

    if charset_type == 1: # extended type
        charset[1] = "%$-;" + charset[1]
        charset[2] = "sn()" + charset[2]

    if charset_type == 2: # maximum type
        charset += ['!"#$%&\()*+,-./:;<=>?@[\\]^_{|}~'] # string.punctuation

    mixed_charset = mixed = ''
    k = 0
    while True:
        for i in range(0, len(charset)): mixed += charset[i][k:k+1]
        if not mixed: break
        mixed_charset += mixed
        mixed = ''
        k+=1

    return mixed_charset

def de_bruijn(charset, n, maxlen):
    """
    Generate the De Bruijn Sequence up to `maxlen` characters for the charset `charset`
    and subsequences of length `n`.
    Algorithm modified from wikipedia http://en.wikipedia.org/wiki/De_Bruijn_sequence
    """
    k = len(charset)
    a = [0] * k * n
    sequence = []
    def db(t, p):
        if len(sequence) == maxlen:
            return

        if t > n:
            if n % p == 0:
                for j in range(1, p + 1):
                    sequence.append(charset[a[j]])
                    if len(sequence) == maxlen:
                        return
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)
    db(1,1)
    return ''.join(sequence)

@memoized
def cyclic_pattern(size=None, start=None, charset_type=None):
    """
    Generate a cyclic pattern

    Args:
        - size: size of generated pattern (Int)
        - start: the start offset of the generated pattern (Int)
        - charset_type: charset type
            0: basic (0-9A-za-z)
            1: extended (default)
            2: maximum (almost printable chars)

    Returns:
        - pattern text (String)
    """
    charset = config.Option.get("p_charset")
    if not charset:
        charset = cyclic_pattern_charset(charset)
    else:
        charset = ''.join(set(charset))

    if start is None:
        start = 0
    if size is None:
        size = 0x10000

    size+=start

    pattern = de_bruijn(charset, 3, size)

    return pattern[start:size]

@memoized
def cyclic_pattern_offset(value):
    """
    Search a value if it is a part of cyclic pattern

    Args:
        - value: value to search for (String/Int)

    Returns:
        - offset in pattern if found
    """
    pattern = cyclic_pattern()
    if to_int(value) is None:
        search = value
    else:
        search = hex2str(to_int(value))

    pos = pattern.find(search)
    return pos if pos != -1 else None

def cyclic_pattern_search(buf):
    """
    Search all cyclic pattern pieces in a buffer

    Args:
        - buf: buffer to search for (String)

    Returns:
        - list of tuple (buffer_offset, pattern_len, pattern_offset)
    """
    result = []
    pattern = cyclic_pattern()

    p = re.compile("[%s]{4,}" % re.escape(cyclic_pattern_charset()))
    found = p.finditer(buf)
    found = list(found)
    for m in found:
        s = buf[m.start():m.end()]
        i = pattern.find(s)
        k = 0
        while i == -1 and len(s) > 4:
            s = s[1:]
            k += 1
            i = pattern.find(s)
        if i != -1:
            result += [(m.start()+k, len(s), i)]

    return result
