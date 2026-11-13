**English** | [Русский](README.ru.md)

# base-agent

A local agent for working with your notes. It is a terminal chat with a single project folder: an Obsidian vault folder or any other folder with files. The agent reads the project files, answers questions about them and creates new notes. Its field of work is set by a role (engineer, tutor, editor, and so on), and a new role is just a new file. The language the agent speaks is set in its settings.

The agent is a single Python file. No packages to install, the models run locally, and your data never leaves your machine.

## Requirements

- **Python 3.9 or newer.** Standard library only, no `pip install`.
- **A local model server with an OpenAI-compatible API.** The default is [Docker Model Runner](https://docs.docker.com/ai/model-runner/); Ollama, llama.cpp server, LM Studio and others work too.
- **Two models:**
  - for answers: Qwen3.8-27B by default (Q4_K_M, about 18 GB). Needs roughly 24 GB of free RAM (Mac) or video memory;
  - for semantic search: Qwen3-Embedding-0.6B by default (about 0.6 GB).

  On weaker hardware, set a smaller model in `.env`.

## Installation

### 1. Get the agent

Copy the `base-agent` folder anywhere. All agent files live inside it; their paths are resolved relative to `agent.py`.

### 2. Start the model server (Docker Model Runner)

**macOS and Windows** — Docker Desktop 4.40 or newer:

```bash
docker desktop enable model-runner --tcp 12434
```

**Linux** — Docker Engine with the Model Runner plugin:

```bash
sudo apt-get install docker-model-plugin
```

Pull the models:

```bash
docker model pull huggingface.co/ggml-org/qwen3.8-27b-gguf:Q4_K_M
docker model pull huggingface.co/qwen/qwen3-embedding-0.6b-gguf:Q8_0
docker model ls
```

Check that the server responds:

```bash
curl http://localhost:12434/engines/v1/models
```

If you use another server (Ollama, llama.cpp, LM Studio), pull the models there and set the address and model names in `.env` (see below).

### 3. Configure

```bash
cd base-agent
cp .env.example .env
```

Edit `.env` and `settings.json` (see "Settings"). With the defaults (Docker Model Runner and the models above), `.env` is optional.

### 4. Run

```bash
python3 agent.py "/path/to/project/folder"
python3 agent.py "/path/to/project/folder" --role study
python3 agent.py "My project"
```

The third form works when `PROJECTS_DIR` is set in `.env`: a relative path is looked up inside it.

Without a path, the program asks for one and remembers it, so next time just press Enter. You can drag a folder from your file manager into the terminal. If the folder does not exist, the program offers to create it, together with any missing parent folders. This is a quick way to start a new project right from the agent.

## Settings

All settings files live next to `agent.py`.

### `.env` — server, models, projects folder

See `.env.example` for a template.

| Variable | What it sets | Default |
|---|---|---|
| `MODEL_BASE_URL` | address of the OpenAI-compatible model server | `http://localhost:12434/engines/v1` (Docker Model Runner) |
| `MODEL` | model for answers | `huggingface.co/ggml-org/qwen3.8-27b-gguf:Q4_K_M` |
| `EMBED_MODEL` | embedding model for search | `huggingface.co/qwen/qwen3-embedding-0.6b-gguf:Q8_0` |
| `PROJECTS_DIR` | folder that short project paths are resolved against | not set |

Other servers: Ollama — `http://localhost:11434/v1`, llama.cpp server — `http://localhost:8080/v1`, LM Studio — `http://localhost:1234/v1`.

`.env` format: `NAME=value` lines; comments start with `#`; quotes around values are optional. A variable set in the shell (`MODEL=… python3 agent.py …`) takes precedence over `.env`. `.env` is personal and already excluded in `.gitignore`.

### `settings.json` — agent name and language

```json
{
 "name": "Alex",
 "language": "English"
}
```

- `name`: the agent signs its answers with this name, and the model knows what it is called.
- `language`: the language the agent speaks, including the text of the files it creates. Use any language name the model understands: `English`, `Russian`, `German`, `Spanish`… If empty, the agent replies in the language you write in.

The file is re-read before every answer, so changes apply without a restart. If the file is missing or broken, the agent is called "Agent" and replies in your language.

### Project role

The role is remembered separately for each project. On first opening a project, the program lists the roles and asks which one to use (Enter = `general`). The `--role` flag picks a role at launch and remembers it; the `/role` command changes it in the chat.

## Chat commands

The input line starts with `>`, the agent's answer with its name: `Alex> …`.

| Command | What it does |
|---|---|
| `/files` | list the project files the agent reads; marks files whose text could not be extracted |
| `/role` | show the current role and the list of roles |
| `/role <name>` | change the role (the conversation continues) |
| `/save` | save the last answer as a new file |
| `/transcript` | save the whole current conversation as a new note in the project |
| `/reset` | start a new conversation; the previous one is archived, not deleted |
| `/exit` or Ctrl+D | quit; the conversation is saved |

While the model has not started answering, a spinner with a seconds counter is shown (`⠹ Alex is thinking… 4 s`); the same while revising a draft and indexing notes. Ctrl+C while waiting or during an answer interrupts it.

## Roles

Roles live in the `roles/` folder, one `.md` file per role. The file name is the role name for `--role` and `/role`. Built-in roles:

- `general` — notes assistant: answers, summaries, plans, outlines;
- `engineer` — electronics engineer;
- `study` — tutor: explains, makes summaries and flashcards, checks solutions with hints;
- `editor` — literary editor.

To add a role, create a new file in `roles/`, for example `roles/lawyer.md`:

```markdown
---
search: Find a passage from the contract or notes that helps answer the question
---
You are a legal consultant. Rely on the documents in the project, cite specific clauses, never invent laws. If the question needs professional legal advice, say so.
```

- `search`: instruction for semantic search, i.e. what to look for in the notes. If missing, a general one is used.
- The text after `---`: the character and rules for this field. Do not put the name or language here: the agent takes them from `settings.json`. The text goes at the start of the model's instructions, before the general rules from `prompt.md`.

Role files and `prompt.md` are written in English, but that does not affect the agent's language: it replies in the language from `settings.json`. A role file is re-read before every answer, so edits take effect immediately.

## What it can do

- **Reads the project.** Before every answer, it re-reads the project files from disk, except hidden ones (`.obsidian`, `.trash`) and temporary Office files (`~$…`). Edits made in your editor are visible immediately. Formats:
  - as text: `.md`, `.txt`, `.csv`, `.tsv`, `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.conf`, `.log`, `.xml`, `.tex` and code (`.py`, `.c`, `.h`, `.cpp`, `.hpp`, `.ino`, `.js`, `.ts`, `.sh`, `.sql`);
  - `.xlsx`: all sheets row by row, cells separated by tabs; `.pptx`: text slide by slide;
  - `.pdf`: via `pdftotext` if installed (macOS: `brew install poppler`, Linux: `sudo apt install poppler-utils`), otherwise via the built-in PDFKit on macOS. Scans without a text layer yield no text;
  - `.docx`, `.doc`, `.rtf`, `.odt`, `.html`, `.htm`: via `textutil`, which exists **only on macOS**. On Linux and Windows these files are skipped for now (`/files` marks them as unreadable).

  At most 300,000 characters are taken from one file. Extracted text is cached while the program runs, so PDFs and documents are parsed again only after they change.
- **Semantic search.** If the whole project is shorter than 8,000 characters, the model gets all of it. Otherwise the text is split into chunks of about 1,200 characters (long tables and logs are split by lines), and the model gets the 6 chunks most similar to the question, using the search instruction from the role. Vectors are cached in `.cache/`; only changed chunks are recomputed. If the embedding model is unavailable, the agent uses the beginning of the notes.
- **Creates new files.** The model puts a draft into a `~~~file: Folder/Title` block and proposes the name itself. After the answer the program asks: `Create file "Folder/Title.md"? Enter = create, or type your remarks`.
  - **Enter**: the file is created with the proposed name, even if the model asked clarifying questions.
  - **Remarks** (or answers to the model's questions): the model revises the draft but does not print it again. The program shows only the changes (`+` added, `−` removed; green and red in the terminal) and offers to create the file again. Repeat as many times as needed.
  - **`/name <title>`** sets a different name; **`/cancel`** (or Ctrl+C) skips the file.

  `.md` is added automatically. Files are created only inside the project folder. Existing files are never overwritten: if the name is taken, " 2", " 3"… is appended. If the model did not propose a name, the first heading of the text or "Note <date>" is used. The block may also use ``` instead of `~~~`. If you asked to write something down but the answer has no block, the program says so and suggests `/save`, which offers the same name-and-remarks flow. `/transcript` proposes the name "Conversation <date time>".
- **Saves conversations.** The conversation with each project is written to disk after every answer and resumes on the next launch: the program shows how many messages there are and what the last question was. The model sees the last 20 messages; the whole conversation is kept on disk. `/reset` archives the conversation, `/transcript` saves it into the project as a note.

Not supported: modifying, appending to or deleting existing files; searching outside the project folder; text recognition in images and scans; a web interface.

## Platforms

- **macOS**: fully supported.
- **Linux**: everything works except reading `.docx`, `.doc`, `.rtf`, `.odt` and `.html`. PDFs need `poppler-utils`.
- **Windows**: not tested. The code is OS-independent; limitations are the same as on Linux. PDFs need `pdftotext` from poppler on `PATH`. The spinner needs a terminal with ANSI support (Windows Terminal).

## How it works

| File | Contents |
|---|---|
| `agent.py` | all the code: settings, models, roles, file reading, file creation, conversations, chat |
| `.env` / `.env.example` | server, models, projects folder; `.env` is personal, `.env.example` is a template |
| `settings.json` | agent settings: name and language |
| `prompt.md` | general rules for the model in any role: working with notes and creating files |
| `roles/` | roles, one file per field |
| `.cache/` | note vectors, last opened project, project roles (`agents.json`) |
| `.cache/chats/` | conversations per project; `archive/` holds them after `/reset` |
| `.gitignore` | excludes `.env`, `.cache/` and `backups/` from git |
| `README.md` / `README.ru.md` | this guide in English / Russian |

One agent step: question → the role file is re-read, and the agent name and language from `settings.json` are added → `context()` collects the file list and the relevant passages → `chat()` sends the role, general rules, notes and history to the model and prints the answer → `create_files()` finds `~~~file` blocks in the answer, `offer_file()` offers to create each one with the name proposed by the model and revises the draft by remarks via `make_reviser()` → the conversation is written to disk.

Deleting `.cache/` is only partly safe: vectors are recomputed automatically, but `chats/` holds your conversations and `agents.json` the project roles.
