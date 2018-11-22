#  Copyright (C) 2016 - Yevgen Muntyan
#  Copyright (C) 2016 - Ignacio Casal Quinteiro
#  Copyright (C) 2016 - Arnavion
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, see <http://www.gnu.org/licenses/>.

"""
Base project class, used also for tools
"""

import os
import shutil
import re
import datetime

from .utils import _rmtree_error_handler
from .simple_ui import log

GVSBUILD_NONE = -1
GVSBUILD_IGNORE = 0
GVSBUILD_PROJECT = 1
GVSBUILD_TOOL = 2
GVSBUILD_GROUP = 3

class Options(object):
    def __init__(self):
        # Only the one used by the projects
        self.enable_gi = False
        self.gtk3_ver = '3.24'
        self.ffmpeg_enable_gpl = False
        # Default
        self._load_python = False

class Project(object):
    def __init__(self, name, **kwargs):
        object.__init__(self)
        self.name = name
        self.prj_dir = name
        self.dependencies = []
        self.patches = []
        self.archive_url = None
        self.archive_file_name = None
        self.tarbomb = False
        self.type = GVSBUILD_NONE
        self.version = None
        self.mark_file = None
        self.clean = False
        self.to_add = True
        for k in kwargs:
            setattr(self, k, kwargs[k])
        self.__working_dir = None
        if not self.version:
            self._calc_version()
        if len(self.name) > Project.name_len:
            Project.name_len = len(self.name)

    _projects = []
    _names = []
    _dict = {}
    _ver_res = None
    name_len = 0
    # List of class/type to add, now not at import time but after some options are parsed
    _reg_prj_list = []
    # build option
    opts = Options()

    def __str__(self):
        return self.name

    def __repr__(self):
        return repr(self.name)

    def load_defaults(self):
        # Used by the tools to load default paths/filenames
        pass

    def finalize_dep(self, builder, deps):
        """
        Used to manipulate the dependencies list, to add or remove projects
        For the dev-shell project is used to limit the tools to use
        """
        pass

    def build(self):
        raise NotImplementedError('build')

    def post_install(self):
        pass

    def add_dependency(self, dep):
        self.dependencies.append(dep)

    def exec_cmd(self, cmd, working_dir=None, add_path=None):
        self.builder.exec_cmd(cmd, working_dir=working_dir, add_path=add_path)

    def exec_vs(self, cmd, add_path=None):
        self.builder.exec_vs(cmd, working_dir=self._get_working_dir(), add_path=add_path)

    def exec_msbuild(self, cmd, configuration=None, add_path=None):
        if not configuration:
            configuration = '%(configuration)s'
        self.exec_vs('msbuild ' + cmd + ' /p:Configuration=' + configuration + ' %(msbuild_opts)s', add_path=add_path)

    def exec_msbuild_gen(self, base_dir, sln_file, add_pars='', configuration=None, add_path=None):
        '''
        looks for base_dir\{vs_ver}\sln_file or base_dir\{vs_ver_tear}\sln_file for launching the msbuild commamd.
        If it's not present in the directory the system start to look backward to find the first version present
        '''
        def _msbuild_ok(self, dir_part):
            print(self.build_dir, base_dir, dir_part, sln_file, sep='\n')
            full = os.path.join(self.build_dir, base_dir, dir_part, sln_file)
            return os.path.exists(full)

        part = 'vs' + self.builder.opts.vs_ver
        if not _msbuild_ok(self, part):
            part = self.builder.vs_ver_year
            if not _msbuild_ok(self, part):
                part = None

        if not part:
            look = {
                '12': [],
                '14': [ 'vs12', 'vs2013', ],
                '15': [ 'vs14', 'vs2015', 'vs12', 'vs2013', ],
                '16': [ 'vs15', 'vs2017', 'vs14', 'vs2015', 'vs12', 'vs2013', ],
                }
            lst = look.get(self.builder.opts.vs_ver, [])
            for p in lst:
                if _msbuild_ok(self, p):
                    part = p
                    break
            if part:
                # We log what we found because is not the default
                log.log('Project %s, using %s directory' % (self.name, part, ))

        if part:
            cmd = os.path.join(base_dir, part, sln_file)
            if add_pars:
                cmd += ' ' + add_pars
        else:
            log.error_exit("Solution file '%s' for project '%s' not found!" % (sln_file, self.name, ))
        self.exec_msbuild(cmd, configuration, add_path)
        return part

    def install(self, *args):
        self.builder.install(self._get_working_dir(), self.pkg_dir, *args)

    def install_dir(self, src, dest=None):
        if not dest:
            dest = os.path.basename(src)
        self.builder.install_dir(self._get_working_dir(), self.pkg_dir, src, dest)

    def patch(self):
        for p in self.patches:
            name = os.path.basename(p)
            stamp = os.path.join(self.build_dir, name + ".patch-applied")
            if not os.path.exists(stamp):
                log.log("Applying patch %s" % (p,))
                self.builder.exec_msys(['patch', '-p1', '-i', p], working_dir=self._get_working_dir())
                with open(stamp, 'w') as stampfile:
                    stampfile.write('done')
            else:
                log.debug("patch %s already applied, skipping" % (p,))

    def _get_working_dir(self):
        if self.__working_dir:
            return os.path.join(self.build_dir, self.__working_dir)
        else:
            return self.build_dir

    def push_location(self, path):
        self.__working_dir = path

    def pop_location(self):
        self.__working_dir = None

    def prepare_build_dir(self):
        if self.clean and os.path.exists(self.build_dir):
            shutil.rmtree(self.build_dir, onerror=_rmtree_error_handler)

        if os.path.exists(self.build_dir):
            log.debug("directory %s already exists" % (self.build_dir,))
            if self.update_build_dir():
                self.mark_file_remove()
                if os.path.exists(self.patch_dir):
                    log.log("Copying files from %s to %s" % (self.patch_dir, self.build_dir))
                    self.builder.copy_all(self.patch_dir, self.build_dir)
        else:
            self.unpack()
            if os.path.exists(self.patch_dir):
                log.log("Copying files from %s to %s" % (self.patch_dir, self.build_dir))
                self.builder.copy_all(self.patch_dir, self.build_dir)

    def update_build_dir(self):
        pass

    def unpack(self):
        raise NotImplementedError("unpack")

    def get_path(self):
        # Optional for projects
        pass

    @staticmethod
    def add(proj, type=GVSBUILD_IGNORE):
        if proj.name in Project._dict:
            log.error_exit("Project '%s' already present!" % (proj.name, ))
        Project._projects.append(proj)
        Project._names.append(proj.name)
        Project._dict[proj.name] = proj
        if proj.type == GVSBUILD_NONE:
            proj.type = type

    @staticmethod
    def register(cls, ty):
        """
        Register the class to be added after some initialization
        """
        Project._reg_prj_list.append((cls, ty, ))

    @staticmethod
    def add_all():
        """
        Add all the registered class
        """
        for cls, ty in Project._reg_prj_list:
            c_inst = cls()
            if c_inst.to_add:
                Project.add(c_inst, type=ty)
            else:
                del c_inst
        del Project._reg_prj_list

    def ignore(self):
        """
        Mark the project not to build/add to the list
        """
        self.to_add = False

    @staticmethod
    def get_project(name):
        return Project._dict[name]

    @staticmethod
    def list_projects():
        return list(Project._projects)

    @staticmethod
    def get_names():
        return list(Project._names)

    @staticmethod
    def get_dict():
        return dict(Project._dict)

    @staticmethod
    def get_tool_path(tool):
        if not isinstance(tool, Project):
            tool = Project._dict[tool]
        if tool.type == GVSBUILD_TOOL:
            t = tool.get_path()
            if isinstance(t, tuple):
                # Get the one that's not null
                return t[0] if t[0] else t[1]
            else:
                return t
        else:
            return None

    @staticmethod
    def get_tool_executable(tool):
        if not isinstance(tool, Project):
            tool = Project._dict[tool]

        if tool.type == GVSBUILD_TOOL:
            return tool.get_executable()
        return None

    @staticmethod
    def get_tool_base_dir(tool):
        if not isinstance(tool, Project):
            tool = Project._dict[tool]

        if tool.type == GVSBUILD_TOOL:
            return tool.get_base_dir()
        return None

    @staticmethod
    def _file_to_version(file_name):
        if not Project._ver_res:
            Project._ver_res = [
                re.compile('.*_v([0-9]+_[0-9]+)\.'),
                re.compile('.*-([0-9+]\.[0-9]+\.[0-9]+-[0-9]+)\.'),
                re.compile('.*-([0-9+]\.[0-9]+\.[0-9]+)-'),
                re.compile('.*-([0-9+]\.[0-9]+\.[0-9]+[a-z])\.'),
                re.compile('.*-([0-9+]\.[0-9]+\.[0-9]+)\.'),
                re.compile('.*-([0-9+]\.[0-9]+)\.'),
                re.compile('.*_([0-9+]\.[0-9]+\.[0-9]+)\.'),
                re.compile('^([0-9+]\.[0-9]+\.[0-9]+)\.'),
                re.compile('^v([0-9+]\.[0-9]+\.[0-9]+\.[0-9]+)\.'),
                re.compile('^v([0-9+]\.[0-9]+\.[0-9]+)\.'),
                re.compile('^v([0-9+]\.[0-9]+)\.'),
                re.compile('.*-([0-9a-f]+)\.'),
                re.compile('.*([0-9]\.[0-9]+)\.'),
                ]

        ver = ''
        for r in Project._ver_res:
            ok = r.match(file_name)
            if ok:
                ver = ok.group(1)
                break
        log.debug('Version from file name:%-16s <- %s' % (ver, file_name, ))
        return ver

    def _calc_version(self):
        if self.archive_file_name:
            self.version = Project._file_to_version(self.archive_file_name)
        elif self.archive_url:
            _t, name = os.path.split(self.archive_url)
            self.version = Project._file_to_version(name)
        else:
            if hasattr(self, 'tag') and self.tag:
                self.version = 'git/' + self.tag
            elif hasattr(self, 'repo_url'):
                self.version = 'git/master'
            else:
                self.version = ''

    def mark_file_calc(self):
        if not self.mark_file:
            self.mark_file = os.path.join(self.build_dir, '.wingtk-built')

    def mark_file_remove(self):
        self.mark_file_calc()
        if os.path.isfile(self.mark_file):
            os.remove(self.mark_file)

    def mark_file_write(self):
        self.mark_file_calc()
        try:
            with open(self.mark_file, 'wt') as fo:
                now = datetime.datetime.now().replace(microsecond=0)
                fo.write('%s\n' % (now.strftime('%Y-%m-%d %H:%M:%S'), ))
        except FileNotFoundError as e:
            log.debug("Exception writing file '%s' (%s)" % (self.mark_file, e, ))

    def mark_file_exist(self):
        rt = None
        self.mark_file_calc()
        if os.path.isfile(self.mark_file):
            try:
                with open(self.mark_file, 'rt') as fi:
                    rt = fi.readline().strip('\n')
            except IOError as e:
                print("Exception reading file '%s'" % (self.mark_file, ))
                print(e)
        return rt

    def is_project(self):
        return self.type == GVSBUILD_PROJECT

def project_add(cls):
    """
    Class decorator to add the newly created Project class to the global projects/tools/groups list
    """
    Project.register(cls, GVSBUILD_PROJECT)
    return cls
