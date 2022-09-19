"""Release notes runner class"""
import os
import os.path
import re
import shutil
import subprocess
import logging
import sys
import time
from collections import defaultdict

import yaml.representer

yaml.add_representer(defaultdict, yaml.representer.Representer.represent_dict)

DEFAULT_CONFIG = {"release_tag_re": r"^v?((?:[\d.ab]|rc)+)"}

log = logging.getLogger("relnotes")


def normalize(git_dir):
    return git_dir.replace("\\", "/").replace("./", "")


class Runner:  # pylint: disable=too-many-instance-attributes
    """Process relnotes command line args."""

    def __init__(self, args):
        self.args = args
        try:
            self.cfg = yaml.safe_load(open("./relnotes.yaml"))
        except FileNotFoundError:
            self.cfg = DEFAULT_CONFIG.copy()

        self.prelude_name = self.cfg.get("prelude_section_name", "release_summary")
        self.earliest = self.cfg.get("earliest_version")
        self.version_regex = (
            args.version_regex
            or self.cfg.get("release_tag_re")
            or DEFAULT_CONFIG.get("release_tag_re")
        )
        self.tags = []
        self.logs = []
        self.notes = {}
        self.report = ""
        self.ver_start = self.args.previous
        self.ver_end = self.args.version or "HEAD"
        self.notes_dir = normalize(self.args.rel_notes_dir)

        log.debug("notes_dir: %s", self.notes_dir)
        if not os.path.exists(self.notes_dir):
            raise FileNotFoundError("expected folder: %s" % self.notes_dir)

        self.sections = dict(self.cfg.get("sections", {}))
        self.valid_sections = {self.prelude_name, *self.sections.keys()}

        self.__git = shutil.which("git")

    def git(self, *args):
        """Shell git with args."""
        log.debug("+ git %s", " ".join(args))
        cmd = [self.__git] + list(args)
        ret = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, encoding="utf8")
        return ret.stdout

    def get_tags(self):
        """Get release tags, reverse sorted."""
        self.tags = []

        for tag in self.git("log", self.ver_end, "--tags", "--pretty=%D").split("\n"):
            tag = tag.strip()
            if not tag:
                continue
            head = re.match(r"HEAD[^,]*, tag:", tag)
            tag = re.search(r"\btag: ([^\s,]+)", tag)
            if not tag:
                continue
            tag = tag[1]
            if re.match(self.version_regex, tag):
                self.tags.append(tag)
                if head:
                    self.ver_end = tag
            if tag == self.earliest:
                break

        self.tags = list(reversed(self.tags))

        log.debug("tags: %s", self.tags)

    def get_start_from_end(self):
        """If start not specified, assume previous release."""
        if not self.ver_start:
            if self.ver_end == "HEAD":
                self.ver_start = self.tags[-1] if self.tags else "HEAD"

            prev = None
            for t in self.tags:
                if self.ver_end == t:
                    self.ver_start = prev
                prev = t

        log.debug("prev: %s, cur: %s", self.ver_start, self.ver_end)

    def get_logs(self):
        """Get a list of logs with tag, hash and ct."""
        cur_tag = self.ver_end
        ct = 0
        cname = ""
        hsh = ""
        vers = self.ver_start + ".." + self.ver_end
        if self.ver_start == "TAIL":
            vers = self.ver_end
        for ent in self.git(
            "log", vers, "--name-only", "--format=%D^%ct^%cn^%h", "--diff-filter=A"
        ).split("\n"):
            ent = ent.strip()
            info = ent.split("^")
            if len(info) > 1:
                tag, ct, cname, hsh = info
                tag = re.match(r"^tag: ([\S,]+)", tag)
                if tag:
                    cur_tag = tag[1]
            if ent.startswith(self.notes_dir):
                self.logs.append((cur_tag, ct, cname, hsh, ent))
        log.debug("logs %s", self.logs)

    def load_note(self, tag, file, ct, cname, hsh, notes):
        try:
            with open(file) as f:
                note = yaml.safe_load(f)
                for k, v in note.items():
                    assert k in self.valid_sections, "%s: %s is not a valid section" % (
                        file,
                        k,
                    )
                    if type(v) is str:
                        v = [v]
                    assert (
                        type(v) is list
                    ), "%s: '%s' : list of entries or single string" % (file, k)
                    for line in v:
                        assert (
                            type(line) is str
                        ), "%s: '%s' : must be a simple string" % (file, line)
                        line = {
                            "time": int(ct),
                            "name": cname,
                            "hash": hsh,
                            "note": line,
                        }
                        notes[tag][k].append(line)
        except Exception as e:
            print("Error reading file %s: %s" % (file, repr(e)))
            raise

    def get_notes(self):
        seen = {}
        notes = defaultdict(lambda: defaultdict(lambda: []))
        for tag, ct, cname, hsh, file in self.logs:
            if seen.get(file):
                continue
            seen[file] = True
            try:
                self.load_note(tag, file, ct, cname, hsh, notes)
            except FileNotFoundError:
                pass

        cname = self.git("config", "user.name").strip()

        for file in self.git("diff", "--name-only", "--cached").split("\n"):
            path = file.strip()
            self._load_uncommitted(seen, notes, path, cname)

        if self.args.lint:
            # every file, not just diffs
            for file in os.listdir(self.notes_dir):
                path = normalize(os.path.join(self.notes_dir, file))
                self._load_uncommitted(seen, notes, path, cname)

        self.notes = notes

    def _load_uncommitted(self, seen, notes, path, cname):
        if seen.get(path):
            return
        if not os.path.isfile(path):
            return
        if not path.endswith(".yaml"):
            return
        if not path.startswith(self.notes_dir):
            return
        self.load_note("Uncommitted", path, os.stat(path).st_mtime, cname, None, notes)

    def get_report(self):
        num = 0
        for tag, sections in self.notes.items():
            if tag == "HEAD":
                tag = "Current Branch"
            if num > 0:
                print("")
            num += 1
            print(tag)
            print("=" * len(tag))

            ents = sections.get(self.prelude_name, {})
            for ent in sorted(ents, key=lambda ent: ent["time"], reverse=True):
                note = ent["note"].strip()
                print(note, "\n")

            for sec, title in self.sections.items():
                ents = sections.get(sec, {})
                if not ents:
                    continue
                print()
                print(title)
                print("-" * len(title))
                for ent in sorted(ents, key=lambda ent: ent["time"], reverse=True):
                    note = ent["note"]
                    if self.args.blame:
                        epoch = ent["time"]
                        name = ent["name"]
                        hsh = ent["hash"]
                        hsh = "`" + hsh + "`" if hsh else ""
                        print(
                            "-",
                            note,
                            hsh,
                            "(" + name + ")",
                            time.strftime("%y-%m-%d", time.localtime(epoch)),
                        )
                    else:
                        print("-", note)

    def get_branch(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def switch_branch(self, branch):
        self.git("-c", "advice.detachedHead=false", "checkout", branch)

    def create_new(self):
        from datetime import datetime
        ymd = datetime.today().strftime('%Y-%m-%d')
        name = ymd + "-" + os.urandom(8).hex() + ".yaml"
        fp = os.path.join(self.notes_dir, name)
        with open(fp, "w", encoding="utf8") as fh:
            fh.write(self.cfg.get("template"))

        # get editor
        editor = self.cfg.get("editor." + sys.platform, self.cfg.get("editor", os.environ.get("VISUAL")))

        if not editor:  # pragma: no cover
            if sys.platform == "win32":
                editor = "notepad"
            else:
                editor = "vi"

        exe = shutil.which(editor)
        cmd = [exe, fp]
        subprocess.run(cmd, check=True)

        answer = input("Add to git [y|n]: ")
        if answer[0].lower() == "y":
            self.git("add", fp)

    def run(self):
        orig = None
        if self.args.create:
            self.create_new()
            return

        if self.ver_end != "HEAD":
            orig = self.get_branch()
            self.switch_branch(self.ver_end)
        try:
            self.get_tags()
            self.get_start_from_end()
            self.get_logs()
            if orig:
                self.switch_branch(orig)
                orig = None
            self.get_notes()
            if self.args.lint:
                return
            if self.args.yaml:
                print(yaml.dump(self.notes))
                return
            self.get_report()

            print(self.report)
        finally:
            if orig:
                self.switch_branch(orig)
