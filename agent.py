#!/usr/bin/env python3
import difflib, hashlib, json, math, os, re, shutil, subprocess, sys, threading, time, unicodedata, urllib.request, zipfile
from datetime import date, datetime
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
CHATS = CACHE / "chats"
ROLES = HERE / "roles"
SETTINGS = HERE / "settings.json"
AGENTS = CACHE / "agents.json"
DEFAULT_ROLE = "general"
DEFAULT_SEARCH = "Find a passage from the notes that helps answer the question"
OLD_ROLES = {"общий": "general", "инженер": "engineer", "учёба": "study", "редактор": "editor"}


def load_env(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env(HERE / ".env")
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "")
BASE = os.environ.get("MODEL_BASE_URL", "http://localhost:12434/engines/v1")
CHAT_MODEL = os.environ.get("MODEL", "huggingface.co/ggml-org/qwen3.8-27b-gguf:Q4_K_M")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "huggingface.co/qwen/qwen3-embedding-0.6b-gguf:Q8_0")

CHUNK = 1200
TOP = 6
FLOOR = 0.3
SMALL = 8000
HISTORY = 20
MAX_DOC = 300000

TEXT_EXT = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
            ".log", ".xml", ".tex", ".py", ".c", ".h", ".cpp", ".hpp", ".ino", ".js", ".ts", ".sh", ".sql"}
CONVERT_EXT = {".docx", ".doc", ".rtf", ".odt", ".html", ".htm"}
DOC_EXT = TEXT_EXT | CONVERT_EXT | {".pdf", ".xlsx", ".pptx"}

BAD_NAME = re.compile(r'[\\:*?"<>|#^\[\]]')
FILE_BLOCK = re.compile(r"^(~~~|```)[ \t]*(?:file|файл)[ \t]*:?[ \t]*([^\n]*)\n(.*?)^\1[ \t]*$", re.S | re.M | re.I)
WANTS_FILE = re.compile(r"\b(save|write|note|file|create)|запиш|записа|сохран|созда|заметк|файл", re.I)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def project_id(project):
    return hashlib.sha256(str(project).encode()).hexdigest()[:16]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text):
        self.text = text
        self.done = threading.Event()
        self.thread = None

    def __enter__(self):
        if sys.stdout.isatty():
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        return self

    def _spin(self):
        start, i = time.monotonic(), 0
        while not self.done.wait(0.1):
            sys.stdout.write("\r\033[K%s %s… %d s" % (self.FRAMES[i % len(self.FRAMES)], self.text,
                                                      time.monotonic() - start))
            sys.stdout.flush()
            i += 1

    def stop(self):
        if self.thread and not self.done.is_set():
            self.done.set()
            self.thread.join()
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        self.done.set()

    def __exit__(self, *exc):
        self.stop()


def post(path, body, timeout):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def chat(messages, echo=True, max_tokens=2000, wait="thinking", prefix=""):
    body = {"model": CHAT_MODEL, "messages": messages, "stream": True, "temperature": 0.3,
            "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}}
    parts, started = [], False
    with Spinner(wait) as spin, post("/chat/completions", body, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            choices = json.loads(data).get("choices") or []
            piece = (choices[0].get("delta") or {}).get("content") or "" if choices else ""
            if echo and piece:
                if not started:
                    spin.stop()
                    print(prefix, end="")
                    started = True
                print(piece, end="", flush=True)
            parts.append(piece)
    if echo:
        print("" if started else prefix)
    return "".join(parts)


def embed(texts):
    out = []
    for i in range(0, len(texts), 16):
        with post("/embeddings", {"model": EMBED_MODEL, "input": texts[i:i + 16]}, timeout=120) as r:
            data = sorted(json.load(r)["data"], key=lambda x: x["index"])
        for item in data:
            v = item["embedding"]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([round(x / n, 6) for x in v])
    return out


def nfc(s):
    return unicodedata.normalize("NFC", s)


def roles():
    return sorted(nfc(p.stem) for p in ROLES.glob("*.md"))


def load_role(name):
    text = (ROLES / (name + ".md")).read_text(encoding="utf-8")
    meta = {}
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            if value.strip():
                meta[key.strip().lower()] = value.strip()
        text = text[m.end():]
    return {"search": meta.get("search", DEFAULT_SEARCH), "text": text.strip()}


def settings():
    data = load_json(SETTINGS, {})
    return data if isinstance(data, dict) else {}


def agent_role(cfg):
    role = load_role(cfg["role"])
    s = settings()
    role["name"] = str(s.get("name", "")).strip() or "Agent"
    role["language"] = str(s.get("language", "")).strip()
    return role


def load_config(project):
    cfg = load_json(AGENTS, {}).get(str(project)) or {}
    role = cfg.get("role") or cfg.get("роль") or load_json(CACHE / "roles.json", {}).get(str(project), "")
    role = OLD_ROLES.get(nfc(role), role)
    return {"role": role} if role else {}


def save_config(project, cfg):
    saved = load_json(AGENTS, {})
    saved[str(project)] = cfg
    save_json(AGENTS, saved)


def setup_agent(project, wanted_role):
    available = roles()
    if not available:
        sys.exit("No roles found in %s" % ROLES)
    role = nfc(wanted_role) or load_config(project).get("role", "")
    while role not in available:
        if role:
            print("No such role: %s" % role)
        default = DEFAULT_ROLE if DEFAULT_ROLE in available else available[0]
        print("Roles: " + ", ".join(available))
        role = nfc(input("Role [Enter = %s]: " % default).strip()) or default
    cfg = {"role": role}
    save_config(project, cfg)
    return cfg


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=True).stdout


def xml_texts(xml, tag):
    return [unescape(t) for t in re.findall(r"<%s(?:\s[^>]*)?>([^<]*)</%s>" % (tag, tag), xml)]


def number(name):
    return int(re.findall(r"\d+", name)[-1])


PDF_JXA = ("ObjC.import('PDFKit'); function run(a) {"
           " var d = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(a[0]));"
           " return d.isNil() ? '' : ObjC.unwrap(d.string); }")


def pdf_text(path):
    if shutil.which("pdftotext"):
        return run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"])
    return run(["osascript", "-l", "JavaScript", "-e", PDF_JXA, str(path)])


def xlsx_text(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = ["".join(xml_texts(si, "t")) for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]
        out = []
        for n in sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)), key=number):
            out.append("## Sheet %d" % number(n))
            xml = z.read(n).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                    v = re.search(r"<v>([^<]*)</v>", body)
                    if 't="s"' in attrs and v:
                        i = int(v.group(1))
                        cells.append(shared[i] if i < len(shared) else "")
                    elif 't="inlineStr"' in attrs:
                        cells.append("".join(xml_texts(body, "t")))
                    else:
                        cells.append(unescape(v.group(1)) if v else "")
                if any(cells):
                    out.append("\t".join(cells).rstrip())
    return "\n".join(out)


def pptx_text(path):
    with zipfile.ZipFile(path) as z:
        out = []
        for n in sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=number):
            xml = z.read(n).decode("utf-8", "replace")
            paras = ["".join(xml_texts(p, "a:t")) for p in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S)]
            out.append("## Slide %d\n%s" % (number(n), "\n".join(p for p in paras if p.strip())))
    return "\n\n".join(out)


def extract(path):
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext in CONVERT_EXT:
        return run(["textutil", "-convert", "txt", "-stdout", str(path)])
    if ext == ".pdf":
        return pdf_text(path)
    if ext == ".xlsx":
        return xlsx_text(path)
    if ext == ".pptx":
        return pptx_text(path)
    return ""


_texts = {}


def read_doc(path):
    st = path.stat()
    key = (st.st_mtime, st.st_size)
    hit = _texts.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        text = extract(path)
    except Exception as e:
        print("(could not read %s: %s)" % (path.name, e))
        text = ""
    text = text.strip()[:MAX_DOC]
    _texts[path] = (key, text)
    return text


def doc_files(project):
    return sorted(p for p in project.rglob("*")
                  if p.is_file() and p.suffix.lower() in DOC_EXT
                  and not any(part.startswith((".", "~$")) for part in p.relative_to(project).parts))


def chunks(project):
    out = []
    for f in doc_files(project):
        rel = str(f.relative_to(project))
        buf = ""
        for para in re.split(r"\n\s*\n", read_doc(f)):
            if buf and len(buf) + len(para) > CHUNK:
                out.append((rel, buf))
                buf = ""
            buf = (buf + "\n\n" + para).strip()
            while len(buf) > CHUNK * 2:
                cut = buf.rfind("\n", 0, CHUNK)
                cut = cut if cut > CHUNK // 2 else CHUNK
                out.append((rel, buf[:cut]))
                buf = buf[cut:].lstrip("\n")
        if buf:
            out.append((rel, buf))
    return out


class Vectors:

    def __init__(self, project):
        CACHE.mkdir(exist_ok=True)
        self.file = CACHE / (project_id(project) + ".json")
        self.mem = load_json(self.file, {})

    def search(self, query, items, instruct):
        texts = [p + "\n" + t for p, t in items]
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        need = [i for i, k in enumerate(keys) if k not in self.mem]
        if need:
            with Spinner("indexing notes: %d chunks" % len(need)):
                vectors = embed([texts[i] for i in need])
            for i, v in zip(need, vectors):
                self.mem[keys[i]] = v
        if need or len(self.mem) != len(set(keys)):
            self.mem = {k: self.mem[k] for k in keys}
            self.file.write_text(json.dumps(self.mem))
        q = embed(["Instruct: %s\nQuery: %s" % (instruct, query[:4000])])[0]
        scored = [(sum(a * b for a, b in zip(q, self.mem[k])), item) for k, item in zip(keys, items)]
        return sorted(scored, key=lambda s: -s[0])


def context(project, vectors, query, instruct):
    items = chunks(project)
    files = [str(f.relative_to(project)) for f in doc_files(project)]
    head = "Today: %s\nProject: %s\nProject files:\n%s" % (
        date.today().isoformat(), project.name, "\n".join("- " + f for f in files) or "(empty so far)")
    if sum(len(t) for _, t in items) <= SMALL:
        picked, how = items, "all project notes"
    else:
        try:
            picked = [item for score, item in vectors.search(query, items, instruct)[:TOP] if score >= FLOOR]
            how = "passages found by meaning of the question"
        except (OSError, ValueError, KeyError) as e:
            print("(semantic search unavailable: %s — using the beginning of the notes)" % e)
            picked, how = items[:TOP], "beginning of the notes (search unavailable)"
    body = "\n\n".join("### %s\n%s" % (p, t) for p, t in picked) or "(nothing relevant found)"
    return "%s\n\nFrom the notes (%s):\n\n%s" % (head, how, body)


def clean_name(name):
    name = BAD_NAME.sub("", name.strip())
    name = "/".join(p.strip() for p in name.split("/") if p.strip() and not p.strip().startswith("."))
    return name[:-3] if name.lower().endswith(".md") else name


def guess_name(text):
    m = re.search(r"^#{1,6}\s+(.+)$", text, re.M)
    return (clean_name(m.group(1))[:80] if m else "") or "Note %s" % date.today().isoformat()


def target(project, name):
    path = (project / (clean_name(name) + ".md")).resolve()
    if project not in path.parents:
        raise ValueError("the file must be inside the project folder")
    base, n = path, 2
    while path.exists():
        path = base.with_name("%s %d.md" % (base.stem, n))
        n += 1
    return path


def show_changes(old, new):
    color = sys.stdout.isatty()
    shown = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        sign, body = line[:1], line[2:]
        if sign not in "+-" or not body.strip():
            continue
        paint = ("\033[32m" if sign == "+" else "\033[31m") if color else ""
        print("%s%s %s%s" % (paint, "+" if sign == "+" else "−", body, "\033[0m" if color else ""))
        shown += 1
    if not shown:
        print("(no changes)")


def offer_file(project, text, name, revise=None):
    name = clean_name(name) or guess_name(text)
    remarks = []
    while True:
        try:
            path = target(project, name)
        except ValueError as e:
            print("Name \"%s\" is not allowed: %s" % (name, e))
            name = "Note %s" % date.today().isoformat()
            continue
        rel = str(path.relative_to(project))
        hint = "Enter = create, or type your remarks" if revise else "Enter = create"
        try:
            reply = input("\nCreate file \"%s\"? %s (/name <title>, /cancel): " % (rel, hint)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            reply = "/cancel"
        if not reply:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as f:
                    f.write(text.strip() + "\n")
            except FileExistsError:
                continue
            print("File created:", rel)
            return "[File created: %s%s]" % (rel, "; remarks applied: " + "; ".join(remarks) if remarks else "")
        if reply in ("/cancel", "/no"):
            print("Not created.")
            return "[User declined to create the file]"
        if reply.startswith("/name"):
            name = clean_name(reply[5:]) or name
            continue
        if not revise:
            print("Options here: Enter, /name <title> or /cancel.")
            continue
        try:
            new_text, new_name = revise(text, rel, reply)
        except KeyboardInterrupt:
            print("(revision interrupted)")
            continue
        except (OSError, ValueError) as e:
            print("The model did not respond: %s" % e)
            continue
        show_changes(text, new_text)
        text, name = new_text, new_name or name
        remarks.append(reply)


def create_files(project, answer, revise):
    return [offer_file(project, m.group(3), m.group(2), revise) for m in FILE_BLOCK.finditer(answer)]


def chat_file(project):
    return CHATS / (project_id(project) + ".json")


def load_chat(project):
    return load_json(chat_file(project), {}).get("messages", [])


def save_chat(project, history):
    save_json(chat_file(project), {"project": str(project), "messages": history})


def archive_chat(project):
    f = chat_file(project)
    if f.exists():
        arch = CHATS / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        f.replace(arch / ("%s-%s.json" % (project_id(project), datetime.now().strftime("%Y%m%d-%H%M%S"))))


def transcript(history):
    parts = []
    for m in history:
        who = "User" if m["role"] == "user" else m.get("who", "Agent")
        parts.append("**%s** · %s\n\n%s" % (who, m.get("time", ""), m["content"]))
    return "# Conversation %s\n\n%s" % (date.today().isoformat(), "\n\n---\n\n".join(parts))


def identity(role):
    lang = role["language"]
    speak = ("Always reply in %s, including the text of files you create, "
             "regardless of the language of these instructions and the notes." % lang if lang
             else "Reply in the language the user writes in.")
    return "Your name is %s. %s" % (role["name"], speak)


def build_messages(project, vectors, role, history, query, extra=None):
    rules = (HERE / "prompt.md").read_text(encoding="utf-8")
    system = "\n\n".join([identity(role), role["text"], rules, context(project, vectors, query, role["search"])])
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY:]]
    if extra:
        messages.append({"role": "user", "content": extra})
    return messages


REVISE = """Revise the draft of the file "%s" according to the user's remarks.

Remarks: %s

Draft:
~~~file: %s
%s
~~~

Return the whole revised file: a single ~~~file block with the file name on its first line, with no explanations before or after the block. Apply the remarks and keep everything else as it was. If the user asks for a different name, change it in the first line of the block."""


def make_reviser(project, vectors, role, history):
    def revise(text, rel, remarks):
        name = rel[:-3] if rel.endswith(".md") else rel
        ask = REVISE % (rel, remarks, name, text.strip())
        answer = chat(build_messages(project, vectors, role, history, remarks, ask), echo=False, max_tokens=6000,
                      wait="revising")
        m = FILE_BLOCK.search(answer)
        if m:
            return m.group(3), clean_name(m.group(2))
        if not answer.strip():
            raise ValueError("empty answer")
        return answer.strip(), ""
    return revise


def parse_args():
    args, role = [], ""
    it = iter(sys.argv[1:])
    for a in it:
        flag, eq, value = a.partition("=")
        if flag == "--role":
            role = value if eq else next(it, "")
        else:
            args.append(a)
    return " ".join(args), role


def pick_project(raw):
    last = CACHE / "last_project"
    if not raw:
        hint = last.read_text().strip() if last.exists() else ""
        raw = input("Project folder%s: " % (" [Enter = %s]" % hint if hint else "")).strip() or hint
    raw = raw.strip().strip("'\"").replace("\\ ", " ")
    project = Path(raw).expanduser()
    if not project.is_absolute() and PROJECTS_DIR:
        project = Path(PROJECTS_DIR).expanduser() / project
    project = project.resolve()
    if project.exists() and not project.is_dir():
        sys.exit("This is a file, not a folder: %s" % project)
    if not project.exists():
        answer = input("Folder does not exist: %s\nCreate it? (y/n): " % project).strip().lower()
        if answer not in ("y", "yes"):
            sys.exit("Not created.")
        project.mkdir(parents=True)
        print("Folder created.")
    CACHE.mkdir(exist_ok=True)
    last.write_text(str(project))
    return project


COMMANDS = "/files  /role [name]  /save  /transcript  /reset  /exit"


def main():
    raw, wanted_role = parse_args()
    project = pick_project(raw)
    cfg = setup_agent(project, wanted_role)
    role = agent_role(cfg)
    vectors = Vectors(project)
    history = load_chat(project)
    last_answer = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    print("Project: %s\nAgent: %s, role: %s, language: %s\nCommands: %s" % (
        project, role["name"], cfg["role"], role["language"] or "as the user writes", COMMANDS))
    if history:
        asked = next((m for m in reversed(history) if m["role"] == "user"), None)
        print("Continuing the conversation: %d messages%s. /reset starts a new one." % (
            len(history), ", last question %s: \"%s\"" % (asked.get("time", ""), asked["content"][:100])
            if asked else ""))
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/files":
            files = doc_files(project)
            for f in files:
                print("- %s%s" % (f.relative_to(project), "" if read_doc(f) else "  (no text extracted)"))
            if not files:
                print("(empty)")
            continue
        if text == "/role" or text.startswith("/role "):
            name = nfc(text[5:].strip())
            if not name:
                print("Current: %s. Roles: %s\nChange: /role <name>" % (cfg["role"], ", ".join(roles())))
            elif name not in roles():
                print("No such role: %s. Available: %s" % (name, ", ".join(roles())))
            else:
                cfg["role"] = name
                save_config(project, cfg)
                role = agent_role(cfg)
                print("Role is now: %s" % name)
            continue
        if text == "/reset":
            archive_chat(project)
            history, last_answer = [], ""
            print("New conversation started; the previous one was archived (project files untouched).")
            continue
        if text == "/save":
            if not last_answer:
                print("Nothing to save yet: no answers so far.")
                continue
            result = offer_file(project, last_answer, guess_name(last_answer),
                                make_reviser(project, vectors, role, history))
            if history:
                history[-1]["content"] += "\n\n" + result
                save_chat(project, history)
            continue
        if text == "/transcript":
            if not history:
                print("The conversation is empty.")
                continue
            offer_file(project, transcript(history), "Conversation %s" % datetime.now().strftime("%Y-%m-%d %H-%M"))
            continue
        if re.fullmatch(r"/[^/\s]+", text.split()[0]):
            print("Unknown command. Commands: %s" % COMMANDS)
            continue

        try:
            role = agent_role(cfg)
        except OSError:
            print("(cannot read role file %s.md, using the previous version)" % cfg["role"])
        history.append({"role": "user", "content": text, "time": now()})
        messages = build_messages(project, vectors, role, history, text)
        print()
        try:
            answer = chat(messages, wait="%s is thinking" % role["name"], prefix="%s> " % role["name"])
        except KeyboardInterrupt:
            print("\n(interrupted)")
            history.pop()
            continue
        except (OSError, ValueError) as e:
            print("\nThe model did not respond: %s\nCheck that the model server is running and reachable: %s"
                  % (e, BASE))
            history.pop()
            continue
        last_answer = answer
        results = create_files(project, answer, make_reviser(project, vectors, role, history))
        if not results and WANTS_FILE.search(text):
            print("\n(The answer has no ~~~file block, so nothing was created. "
                  "Ask again, or type /save to save the last answer.)")
        history.append({"role": "assistant", "content": "\n\n".join([answer] + results),
                        "time": now(), "who": role["name"]})
        save_chat(project, history)


if __name__ == "__main__":
    main()
