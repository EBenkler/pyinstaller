#!/usr/bin/env python
#
# Build packages using spec files
#
# Copyright (C) 2005, Giovanni Bajo
# Based on previous work under copyright (c) 1999, 2002 McMillan Enterprises, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA

import sys
import os
import shutil
import pprint
import time
import py_compile
import tempfile
import md5
import UserList

import mf
import archive
import iu
import carchive
import bindepend

STRINGTYPE = type('')
TUPLETYPE = type((None,))
UNCOMPRESSED, COMPRESSED = range(2)

# todo: use pkg_resources here
HOMEPATH = os.path.dirname(sys.argv[0])
SPECPATH = None
BUILDPATH = None
WARNFILE = None

rthooks = {}
iswin = sys.platform[:3] == 'win'
cygwin = sys.platform == 'cygwin'

def _save_data(filename, data):
    outf = open(filename, 'w')
    pprint.pprint(data, outf)
    outf.close()

def _load_data(filename):
    return eval(open(filename, 'r').read())

def setupUPXFlags():
    f = os.environ.get("UPX", "")
    is24 = hasattr(sys, "version_info") and sys.version_info[:2] >= (2,4)
    if iswin and is24:
        # Binaries built with Visual Studio 7.1 require --strip-loadconf
        # or they won't compress. Configure.py makes sure that UPX is new
        # enough to support --strip-loadconf.
        f = "--strip-loadconf " + f
    f = "--best " + f
    os.environ["UPX"] = f

def mtime(fnm):
    try:
        return os.stat(fnm)[8]
    except:
        return 0

def absnormpath(apath):
    return os.path.abspath(os.path.normpath(apath))

#--- functons for checking guts ---

def _check_guts_eq(attr, old, new, last_build):
    """
    rebuild is required if values differ
    """
    if old != new:
        print "building because %s changed" % attr
        return True
    return False

def _check_guts_toc_mtime(attr, old, toc, last_build, pyc=0):
    """
    rebuild is required if mtimes of files listed in old toc are newer
    than ast_build

    if pyc=1, check for .py files, too
    """
    for (nm, fnm, typ) in old:
        if mtime(fnm) > last_build:
            print "building because %s changed" % fnm
            return True
        elif pyc and mtime(fnm[:-1]) > last_build:
            print "building because %s changed" % fnm[:-1]
            return True
    return False

def _check_guts_toc(attr, old, toc, last_build, pyc=0):
    """
    rebuild is required if either toc content changed if mtimes of
    files listed in old toc are newer than ast_build

    if pyc=1, check for .py files, too
    """
    return    _check_guts_eq       (attr, old, toc, last_build) \
           or _check_guts_toc_mtime(attr, old, toc, last_build, pyc=pyc)

#--

class Target:
    invcnum = 0
    def __init__(self):
        self.invcnum = Target.invcnum
        Target.invcnum += 1
        self.out = os.path.join(BUILDPATH, 'out%s%d.toc' % (self.__class__.__name__,
                                                            self.invcnum))
        self.dependencies = TOC()
    def __postinit__(self):
        print "checking %s" % (self.__class__.__name__,)
        if self.check_guts(mtime(self.out)):
            self.assemble()

    GUTS = []

    def check_guts(self, last_build):
        pass

    def get_guts(self, last_build, missing ='missing or bad'):
        """
        returns None if guts have changed
        """
        try:
            data = _load_data(self.out)
        except:
            print "building because", os.path.basename(self.out), missing
            return None

        if len(data) != len(self.GUTS):
            print "building because %s is bad" % outnm
            return None
        for i in range(len(self.GUTS)):
            attr, func = self.GUTS[i]
            if func is None:
                # no check for this value
                continue
            if func(attr, data[i], getattr(self, attr), last_build):
                return None
        return data


class Analysis(Target):
    def __init__(self, scripts=None, pathex=None, hookspath=None, excludes=None):
        Target.__init__(self)
        self.inputs = scripts
        for script in scripts:
            if not os.path.exists(script):
                raise ValueError, "script '%s' not found" % script
        self.pathex = []
        if pathex:
            for path in pathex:
                self.pathex.append(absnormpath(path))
        self.hookspath = hookspath
        self.excludes = excludes
        self.scripts = TOC()
        self.pure = TOC()
        self.binaries = TOC()
        self.zipfiles = TOC()
        self.__postinit__()
 
    GUTS = (('inputs',    _check_guts_eq),
            ('pathex',    _check_guts_eq),
            ('hookspath', _check_guts_eq),
            ('excludes',  _check_guts_eq),
            ('scripts',   _check_guts_toc_mtime),
            ('pure',      lambda *args: apply(_check_guts_toc_mtime,
                                              args, {'pyc': 1 }   )),
            ('binaries',  _check_guts_toc_mtime),
            ('zipfiles',  _check_guts_toc_mtime),
            )
 
    def check_guts(self, last_build):
        outnm = os.path.basename(self.out)
        if last_build == 0:
            print "building %s because %s non existent" % (self.__class__.__name__, outnm)
            return True
        for fnm in self.inputs:
            if mtime(fnm) > last_build:
                print "building because %s changed" % fnm
                return True

        data = Target.get_guts(self, last_build)
        if not data:
            return True
        scripts, pure, binaries, zipfiles = data[-4:]
        self.scripts = TOC(scripts)
        self.pure = TOC(pure)
        self.binaries = TOC(binaries)
        self.zipfiles = TOC(zipfiles)
        return False

    def assemble(self):
        print "running Analysis", os.path.basename(self.out)
        paths = self.pathex
        for i in range(len(paths)):
            # FIXME: isn't self.pathex already norm-abs-pathed?
            paths[i] = absnormpath(paths[i])
        ###################################################
        # Scan inputs and prepare:
        dirs = {}  # input directories
        pynms = [] # python filenames with no extension
        for script in self.inputs:
            if not os.path.exists(script):
                print "Analysis: script %s not found!" % script
                sys.exit(1)
            d, base = os.path.split(script)
            if not d:
                d = os.getcwd()
            d = absnormpath(d)
            pynm, ext = os.path.splitext(base)
            dirs[d] = 1
            pynms.append(pynm)
        ###################################################
        # Initialize analyzer and analyze scripts
        analyzer = mf.ImportTracker(dirs.keys()+paths, self.hookspath,
                                    self.excludes,
                                    target_platform=target_platform)
        #print analyzer.path
        scripts = [] # will contain scripts to bundle
        for i in range(len(self.inputs)):
            script = self.inputs[i]
            print "Analyzing:", script
            analyzer.analyze_script(script)
            scripts.append((pynms[i], script, 'PYSOURCE'))
        ###################################################
        # Fills pure, binaries and rthookcs lists to TOC
        pure = []     # pure python modules
        binaries = [] # binaries to bundle
        zipfiles = [] # zipfiles to bundle
        rthooks = []  # rthooks if needed
        for modnm, mod in analyzer.modules.items():
            # FIXME: why can we have a mod == None here?
            if mod is not None:
                hooks = findRTHook(modnm)  #XXX
                if hooks:
                    rthooks.extend(hooks)
                if isinstance(mod, mf.BuiltinModule):
                    pass
                else:
                    fnm = mod.__file__
                    if isinstance(mod, mf.ExtensionModule):
                        binaries.append((mod.__name__, fnm, 'EXTENSION'))
                    elif isinstance(mod, mf.PkgInZipModule):
                        zipfiles.append((os.path.basename(str(mod.owner)),
                                         str(mod.owner), 'ZIPFILE'))
                    elif modnm == '__main__':
                        pass
                    else:
                        pure.append((modnm, fnm, 'PYMODULE'))
        binaries.extend(bindepend.Dependencies(binaries))
        self.fixMissingPythonLib(binaries)
        scripts[1:1] = rthooks
        self.scripts = TOC(scripts)
        self.pure = TOC(pure)
        self.binaries = TOC(binaries)
        self.zipfiles = TOC(zipfiles)
        try: # read .toc
            oldstuff = _load_data(self.out)
        except:
            oldstuff = None
        newstuff = (self.inputs, self.pathex, self.hookspath, self.excludes,
                    self.scripts, self.pure, self.binaries, self.zipfiles)
        if oldstuff != newstuff:
            _save_data(self.out, newstuff)
            wf = open(WARNFILE, 'w')
            for ln in analyzer.getwarnings():
                wf.write(ln+'\n')
            wf.close()
            print "Warnings written to %s" % WARNFILE
            return 1
        print self.out, "no change!"
        return 0

    def fixMissingPythonLib(self, binaries):
        """Add the Python library if missing from the binaries.

        Some linux distributions (e.g. debian-based) statically build the
        Python executable to the libpython, so bindepend doesn't include
        it in its output.
        """
        if target_platform != 'linux2': return

        name = 'libpython%d.%d.so' % sys.version_info[:2]
        for (nm, fnm, typ) in binaries:
            if typ == 'BINARY' and name in fnm:
                # lib found
                return

        lib = bindepend.findLibrary(name)
        if lib is None:
            raise IOError("Python library not found!")

        binaries.append((os.path.split(lib)[1], lib, 'BINARY'))


def findRTHook(modnm):
    hooklist = rthooks.get(modnm)
    if hooklist:
        rslt = []
        for script in hooklist:
            nm = os.path.basename(script)
            nm = os.path.splitext(nm)[0]
            if os.path.isabs(script):
                path = script
            else:
                path = os.path.join(HOMEPATH, script)
            rslt.append((nm, path, 'PYSOURCE'))
        return rslt
    return None

class PYZ(Target):
    typ = 'PYZ'
    def __init__(self, toc, name=None, level=9):
        Target.__init__(self)
        self.toc = toc
        self.name = name
        if name is None:
            self.name = self.out[:-3] + 'pyz'
        if config['useZLIB']:
            self.level = level
        else:
            self.level = 0
        self.dependencies = config['PYZ_dependencies']
        self.__postinit__()

    GUTS = (('name',   _check_guts_eq),
            ('level',  _check_guts_eq),
            ('toc',    _check_guts_toc), # todo: pyc=1
            )

    def check_guts(self, last_build):
        outnm = os.path.basename(self.out)
        if not os.path.exists(self.name):
            print "rebuilding %s because %s is missing" % (outnm, os.path.basename(self.name))
            return True

        data = Target.get_guts(self, last_build)
        if not data:
            return True
        return False
    
    def assemble(self):
        print "building PYZ", os.path.basename(self.out)
        pyz = archive.ZlibArchive(level=self.level)
        toc = self.toc - config['PYZ_dependencies']
        for (nm, fnm, typ) in toc:
            if mtime(fnm[:-1]) > mtime(fnm):
                py_compile.compile(fnm[:-1])
        pyz.build(self.name, toc)
        _save_data(self.out, (self.name, self.level, self.toc))
        return 1

def cacheDigest(fnm):
    data = open(fnm, "rb").read()
    digest = md5.new(data).digest()
    return digest

def checkCache(fnm, strip, upx, fix_paths=1):
    # On darwin a cache is required anyway to keep the libaries
    # with relative install names
    if not strip and not upx and sys.platform != 'darwin':
        return fnm
    if strip:
        strip = 1
    else:
        strip = 0
    if upx:
        upx = 1
    else:
        upx = 0

    # Load cache index
    cachedir = os.path.join(HOMEPATH, 'bincache%d%d' %  (strip, upx))
    if not os.path.exists(cachedir):
        os.makedirs(cachedir)
    cacheindexfn = os.path.join(cachedir, "index.dat")
    if os.path.exists(cacheindexfn):
        cache_index = _load_data(cacheindexfn)
    else:
        cache_index = {}

    # Verify if the file we're looking for is present in the cache.
    basenm = os.path.normcase(os.path.basename(fnm))
    digest = cacheDigest(fnm)
    cachedfile = os.path.join(cachedir, basenm)
    cmd = None
    if cache_index.has_key(basenm):
        if digest != cache_index[basenm]:
            os.remove(cachedfile)
        else:
            return cachedfile
    if upx:
        if strip:
            fnm = checkCache(fnm, 1, 0, fix_paths=0)
        cmd = "upx --best -q \"%s\"" % cachedfile
    else:
        if strip:
            cmd = "strip \"%s\"" % cachedfile
    shutil.copy2(fnm, cachedfile)
    os.chmod(cachedfile, 0755)
    if cmd: os.system(cmd)

    if sys.platform == 'darwin' and fix_paths:
        bindepend.fixOsxPaths(cachedfile)

    # update cache index
    cache_index[basenm] = digest
    _save_data(cacheindexfn, cache_index)

    return cachedfile

class PKG(Target):
    typ = 'PKG'
    xformdict = {'PYMODULE' : 'm',
                 'PYSOURCE' : 's',
                 'EXTENSION' : 'b',
                 'PYZ' : 'z',
                 'PKG' : 'a',
                 'DATA': 'x',
                 'BINARY': 'b',
                 'ZIPFILE': 'Z',
                 'EXECUTABLE': 'b'}
    def __init__(self, toc, name=None, cdict=None, exclude_binaries=0,
                 strip_binaries=0, upx_binaries=0):
        Target.__init__(self)
        self.toc = toc
        self.cdict = cdict
        self.name = name
        self.exclude_binaries = exclude_binaries
        self.strip_binaries = strip_binaries
        self.upx_binaries = upx_binaries
        if name is None:
            self.name = self.out[:-3] + 'pkg'
        if self.cdict is None:
            if config['useZLIB']:
                self.cdict = {'EXTENSION':COMPRESSED,
                              'DATA':COMPRESSED,
                              'BINARY':COMPRESSED,
                              'EXECUTABLE':COMPRESSED,
                              'PYSOURCE':COMPRESSED,
                              'PYMODULE':COMPRESSED }
            else:
                self.cdict = { 'PYSOURCE':UNCOMPRESSED }
        self.__postinit__()

    GUTS = (('name',   _check_guts_eq),
            ('cdict',  _check_guts_eq),
            ('toc',    _check_guts_toc_mtime),
            ('exclude_binaries',  _check_guts_eq),
            ('strip_binaries',  _check_guts_eq),
            ('upx_binaries',  _check_guts_eq),
            )

    def check_guts(self, last_build):
        outnm = os.path.basename(self.out)
        if not os.path.exists(self.name):
            print "rebuilding %s because %s is missing" % (outnm, os.path.basename(self.name))
            return 1
        
        data = Target.get_guts(self, last_build)
        if not data:
            return True
        # todo: toc equal
        return False


    def assemble(self):
        print "building PKG", os.path.basename(self.name)
        trash = []
        mytoc = []
        toc = TOC()
        for item in self.toc:
            inm, fnm, typ = item
            if typ == 'EXTENSION':
                binext = os.path.splitext(fnm)[1]
                if not os.path.splitext(inm)[1] == binext:
                    inm = inm + binext
            toc.append((inm, fnm, typ))
        seen = {}
        for inm, fnm, typ in toc:
            if typ in ('BINARY', 'EXTENSION'):
                if self.exclude_binaries:
                    self.dependencies.append((inm, fnm, typ))
                else:
                    fnm = checkCache(fnm, self.strip_binaries,
                                     self.upx_binaries and ( iswin or cygwin )
                                      and config['hasUPX'])
                    # Avoid importing the same binary extension twice. This might
                    # happen if they come from different sources (eg. once from
                    # binary dependence, and once from direct import).
                    if typ == 'BINARY' and seen.has_key(fnm):
                        continue
                    seen[fnm] = 1
                    mytoc.append((inm, fnm, self.cdict.get(typ,0),
                                  self.xformdict.get(typ,'b')))
            elif typ == 'OPTION':
                mytoc.append((inm, '', 0, 'o'))
            else:
                mytoc.append((inm, fnm, self.cdict.get(typ,0), self.xformdict.get(typ,'b')))
        archive = carchive.CArchive()
        archive.build(self.name, mytoc)
        _save_data(self.out,
                   (self.name, self.cdict, self.toc, self.exclude_binaries,
                    self.strip_binaries, self.upx_binaries))
        for item in trash:
            os.remove(item)
        return 1

class EXE(Target):
    typ = 'EXECUTABLE'
    exclude_binaries = 0
    append_pkg = 1
    def __init__(self, *args, **kws):
        Target.__init__(self)
        self.console = kws.get('console',1)
        self.debug = kws.get('debug',0)
        self.name = kws.get('name',None)
        self.icon = kws.get('icon',None)
        self.versrsrc = kws.get('version',None)
        self.strip = kws.get('strip',None)
        self.upx = kws.get('upx',None)
        self.exclude_binaries = kws.get('exclude_binaries',0)
        self.append_pkg = kws.get('append_pkg', self.append_pkg)
        if self.name is None:
            self.name = self.out[:-3] + 'exe'
        if not os.path.isabs(self.name):
            self.name = os.path.join(SPECPATH, self.name)
        if target_iswin or cygwin:
            self.pkgname = self.name[:-3] + 'pkg'
        else:
            self.pkgname = self.name + '.pkg'
        self.toc = TOC()
        for arg in args:
            if isinstance(arg, TOC):
                self.toc.extend(arg)
            elif isinstance(arg, Target):
                self.toc.append((os.path.basename(arg.name), arg.name, arg.typ))
                self.toc.extend(arg.dependencies)
            else:
                self.toc.extend(arg)
        self.toc.extend(config['EXE_dependencies'])
        self.pkg = PKG(self.toc, cdict=kws.get('cdict',None), exclude_binaries=self.exclude_binaries,
                       strip_binaries=self.strip, upx_binaries=self.upx)
        self.dependencies = self.pkg.dependencies
        self.__postinit__()

    GUTS = (('name',     _check_guts_eq),
            ('console',  _check_guts_eq),
            ('debug',    _check_guts_eq),
            ('icon',     _check_guts_eq),
            ('versrsrc', _check_guts_eq),
            ('strip',    _check_guts_eq),
            ('upx',      _check_guts_eq),
            ('mtm',      None,), # checked bellow
            )

    def check_guts(self, last_build):
        outnm = os.path.basename(self.out)
        if not os.path.exists(self.name):
            print "rebuilding %s because %s missing" % (outnm, os.path.basename(self.name))
            return 1
        if not self.append_pkg and not os.path.exists(self.pkgname):
            print "rebuilding because %s missing" % (
                os.path.basename(self.pkgname),)
            return 1

        data = Target.get_guts(self, last_build)
        if not data:
            return True

        icon, versrsrc = data[3:5]
        if (icon or versrsrc) and not config['hasRsrcUpdate']:
            # todo: really ignore :-)
            print "ignoring icon and version resources = platform not capable"

        mtm = data[-1]
        if mtm != mtime(self.name):
            print "rebuilding", outnm, "because mtimes don't match"
            return True
        if mtm < mtime(self.pkg.out):
            print "rebuilding", outnm, "because pkg is more recent"
            return True

        return False

    def _bootloader_postfix(self, exe):
        if target_iswin:
            exe = exe + "_"
            is24 = hasattr(sys, "version_info") and sys.version_info[:2] >= (2,4)
            exe = exe + "67"[is24]
            exe = exe + "rd"[self.debug]
            exe = exe + "wc"[self.console]
        else:
            if not self.console:
                exe = exe + 'w'
            if self.debug:
                exe = exe + '_d'
        return exe
    
    def assemble(self):
        print "building EXE from", os.path.basename(self.out)
        trash = []
        outf = open(self.name, 'wb')
        exe = self._bootloader_postfix('support/loader/run')
        exe = os.path.join(HOMEPATH, exe)
        if target_iswin or cygwin:
            exe = exe + '.exe'
        if config['hasRsrcUpdate']:
            if self.icon:
                tmpnm = tempfile.mktemp()
                shutil.copy2(exe, tmpnm)
                os.chmod(tmpnm, 0755)
                icon.CopyIcons(tmpnm, self.icon)
                trash.append(tmpnm)
                exe = tmpnm
            if self.versrsrc:
                tmpnm = tempfile.mktemp()
                shutil.copy2(exe, tmpnm)
                os.chmod(tmpnm, 0755)
                versionInfo.SetVersion(tmpnm, self.versrsrc)
                trash.append(tmpnm)
                exe = tmpnm
        exe = checkCache(exe, self.strip, self.upx and config['hasUPX'])
        self.copy(exe, outf)
        if self.append_pkg:
            print "Appending archive to EXE", self.name
            self.copy(self.pkg.name, outf)
        else:
            print "Copying archive to", self.pkgname
            shutil.copy2(self.pkg.name, self.pkgname)
        outf.close()
        os.chmod(self.name, 0755)
        _save_data(self.out,
                   (self.name, self.console, self.debug, self.icon,
                    self.versrsrc, self.strip, self.upx, mtime(self.name)))
        for item in trash:
            os.remove(item)
        return 1
    def copy(self, fnm, outf):
        inf = open(fnm, 'rb')
        while 1:
            data = inf.read(64*1024)
            if not data:
                break
            outf.write(data)

class DLL(EXE):
    def assemble(self):
        print "building DLL", os.path.basename(self.out)
        outf = open(self.name, 'wb')
        dll = self._bootloader_postfix('support/loader/inprocsrvr')
        dll = os.path.join(HOMEPATH, dll)  + '.dll'
        self.copy(dll, outf)
        self.copy(self.pkg.name, outf)
        outf.close()
        os.chmod(self.name, 0755)
        _save_data(self.out,
                   (self.name, self.console, self.debug, self.icon,
                    self.versrsrc, self.strip, self.upx, mtime(self.name)))
        return 1


class COLLECT(Target):
    def __init__(self, *args, **kws):
        Target.__init__(self)
        self.name = kws.get('name',None)
        if self.name is None:
            self.name = 'dist_' + self.out[:-4]
        self.strip_binaries = kws.get('strip',0)
        self.upx_binaries = kws.get('upx',0)
        if not os.path.isabs(self.name):
            self.name = os.path.join(SPECPATH, self.name)
        self.toc = TOC()
        for arg in args:
            if isinstance(arg, TOC):
                self.toc.extend(arg)
            elif isinstance(arg, Target):
                self.toc.append((os.path.basename(arg.name), arg.name, arg.typ))
                if isinstance(arg, EXE) and not arg.append_pkg:
                    self.toc.append((os.path.basename(arg.pkgname), arg.pkgname, 'PKG'))
                self.toc.extend(arg.dependencies)
            else:
                self.toc.extend(arg)
        self.__postinit__()

    GUTS = (('name',            _check_guts_eq),
            ('strip_binaries',  _check_guts_eq),
            ('upx_binaries',    _check_guts_eq),
            ('toc',             _check_guts_eq), # additional check below
            )
        
    def check_guts(self, last_build):
        data = Target.get_guts(self, last_build)
        if not data:
            return True
        toc = data[-1]
        for inm, fnm, typ in self.toc:
            if typ == 'EXTENSION':
                ext = os.path.splitext(fnm)[1]
                test = os.path.join(self.name, inm+ext)
            else:
                test = os.path.join(self.name, os.path.basename(fnm))
            if not os.path.exists(test):
                print "building %s because %s is missing" % (outnm, test)
                return 1
            if mtime(fnm) > mtime(test):
                print "building %s because %s is more recent" % (outnm, fnm)
                return 1
        return 0

    def assemble(self):
        print "building COLLECT", os.path.basename(self.out)
        if not os.path.exists(self.name):
            os.mkdir(self.name)
        toc = TOC()
        for inm, fnm, typ in self.toc:
            if typ == 'EXTENSION':
                binext = os.path.splitext(fnm)[1]
                if not os.path.splitext(inm)[1] == binext:
                    inm = inm + binext
            toc.append((inm, fnm, typ))
        for inm, fnm, typ in toc:
            tofnm = os.path.join(self.name, inm)
            todir = os.path.dirname(tofnm)
            if not os.path.exists(todir):
                os.makedirs(todir)
            if typ in ('EXTENSION', 'BINARY'):
                fnm = checkCache(fnm, self.strip_binaries,
                                 self.upx_binaries and ( iswin or cygwin )
                                  and config['hasUPX'])
            shutil.copy2(fnm, tofnm)
            if typ in ('EXTENSION', 'BINARY'):
                os.chmod(tofnm, 0755)
        _save_data(self.out,
                 (self.name, self.strip_binaries, self.upx_binaries, self.toc))
        return 1


class TOC(UserList.UserList):
    def __init__(self, initlist=None):
        UserList.UserList.__init__(self)
        self.fltr = {}
        if initlist:
            for tpl in initlist:
                self.append(tpl)
    def append(self, tpl):
        try:
            fn = tpl[0]
            if tpl[2] == "BINARY":
                # Normalize the case for binary files only (to avoid duplicates
                # for different cases under Windows). We can't do that for
                # Python files because the import semantic (even at runtime)
                # depends on the case.
                fn = os.path.normcase(fn)
            if not self.fltr.get(fn):
                self.data.append(tpl)
                self.fltr[fn] = 1
        except TypeError:
            print "TOC found a %s, not a tuple" % tpl
            raise
    def insert(self, pos, tpl):
        fn = tpl[0]
        if tpl[2] == "BINARY":
            fn = os.path.normcase(fn)
        if not self.fltr.get(fn):
            self.data.insert(pos, tpl)
            self.fltr[fn] = 1
    def __add__(self, other):
        rslt = TOC(self.data)
        rslt.extend(other)
        return rslt
    def __radd__(self, other):
        rslt = TOC(other)
        rslt.extend(self.data)
        return rslt
    def extend(self, other):
        for tpl in other:
            self.append(tpl)
    def __sub__(self, other):
        fd = self.fltr.copy()
        # remove from fd if it's in other
        for tpl in other:
            if fd.get(tpl[0],0):
                del fd[tpl[0]]
        rslt = TOC()
        # return only those things still in fd (preserve order)
        for tpl in self.data:
            if fd.get(tpl[0],0):
                rslt.append(tpl)
        return rslt
    def __rsub__(self, other):
        rslt = TOC(other)
        return rslt.__sub__(self)
    def intersect(self, other):
        rslt = TOC()
        for tpl in other:
            if self.fltr.get(tpl[0],0):
                rslt.append(tpl)
        return rslt

class Tree(Target, TOC):
    def __init__(self, root=None, prefix=None, excludes=None):
        Target.__init__(self)
        TOC.__init__(self)
        self.root = root
        self.prefix = prefix
        self.excludes = excludes
        if excludes is None:
            self.excludes = []
        self.__postinit__()

    GUTS = (('root',     _check_guts_eq),
            ('prefix',   _check_guts_eq),
            ('excludes', _check_guts_eq),
            ('toc',      None),
            )

    def check_guts(self, last_build):
        data = Target.get_guts(self, last_build)
        if not data:
            return True
        stack = [ data[0] ] # root
        toc = data[3] # toc
        while stack:
            d = stack.pop()
            if mtime(d) > last_build:
                print "building %s because directory %s changed" % (outnm, d)
                return True
            for nm in os.listdir(d):
                path = os.path.join(d, nm)
                if os.path.isdir(path):
                    stack.append(path)
        self.data = toc
        return False

    def assemble(self):
        print "building Tree", os.path.basename(self.out)
        stack = [(self.root, self.prefix)]
        excludes = {}
        xexcludes = {}
        for nm in self.excludes:
            if nm[0] == '*':
                xexcludes[nm[1:]] = 1
            else:
                excludes[nm] = 1
        rslt = []
        while stack:
            dir, prefix = stack.pop()
            for fnm in os.listdir(dir):
                if excludes.get(fnm, 0) == 0:
                    ext = os.path.splitext(fnm)[1]
                    if xexcludes.get(ext,0) == 0:
                        fullfnm = os.path.join(dir, fnm)
                        rfnm = prefix and os.path.join(prefix, fnm) or fnm
                        if os.path.isdir(fullfnm):
                            stack.append((fullfnm, rfnm))
                        else:
                            rslt.append((rfnm, fullfnm, 'DATA'))
        self.data = rslt
        try:
            oldstuff = _load_data(self.out)
        except:
            oldstuff = None
        newstuff = (self.root, self.prefix, self.excludes, self.data)
        if oldstuff != newstuff:
            _save_data(self.out, newstuff)
            return 1
        print self.out, "no change!"
        return 0

def TkTree():
    tclroot = config['TCL_root']
    tclnm = os.path.join('_MEI', os.path.basename(tclroot))
    tkroot = config['TK_root']
    tknm = os.path.join('_MEI', os.path.basename(tkroot))
    tcltree = Tree(tclroot, tclnm, excludes=['demos','encoding','*.lib'])
    tktree = Tree(tkroot, tknm, excludes=['demos','encoding','*.lib'])
    return tcltree + tktree

def TkPKG():
    return PKG(TkTree(), name='tk.pkg')

#---

def build(spec):
    global SPECPATH, BUILDPATH, WARNFILE, rthooks
    rthooks = _load_data(os.path.join(HOMEPATH, 'rthooks.dat'))
    SPECPATH, specnm = os.path.split(spec)
    specnm = os.path.splitext(specnm)[0]
    if SPECPATH == '':
        SPECPATH = os.getcwd()
    WARNFILE = os.path.join(SPECPATH, 'warn%s.txt' % specnm)
    BUILDPATH = os.path.join(SPECPATH, 'build%s' % specnm)
    if '-o' in sys.argv:
        bpath = sys.argv[sys.argv.index('-o')+1]
        if os.path.isabs(bpath):
            BUILDPATH = bpath
        else:
            BUILDPATH = os.path.join(SPECPATH, bpath)
    if not os.path.exists(BUILDPATH):
        os.mkdir(BUILDPATH)
    execfile(spec)


def main(specfile, configfilename):
    global target_platform, target_iswin, config
    global icon, versionInfo

    try:
        config = _load_data(configfilename)
    except IOError:
        print "You must run Configure.py before building!"
        sys.exit(1)

    target_platform = config.get('target_platform', sys.platform)
    target_iswin = target_platform[:3] == 'win'

    if target_platform == sys.platform:
        # _not_ cross compiling
        if config['pythonVersion'] != sys.version:
            print "The current version of Python is not the same with which PyInstaller was configured."
            print "Please re-run Configure.py with this version."
            sys.exit(1)

    if config['hasRsrcUpdate']:
        import icon, versionInfo

    if config['hasUPX']:
        setupUPXFlags()

    if not config['useELFEXE']:
        EXE.append_pkg = 0

    build(specfile)

if __name__ == '__main__':
    from optparse import OptionParser
    parser = OptionParser('%prog [options] specfile')
    parser.add_option('-C', '--configfile',
                      default=os.path.join(HOMEPATH, 'config.dat'),
                      help='Name of generated configfile (default: %default)')
    opts, args = parser.parse_args()
    if len(args) != 1:
        parser.error('Requires exactly one .spec-file')

    main(args[0], configfilename=opts.configfile)
