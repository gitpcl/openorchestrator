Here's a complete, professional **Product Specification Document** for **Open Orchestrator v0.3+** ("Enhanced Open Orchestrator").

You can copy-paste this directly into a `SPEC.md`, `ROADMAP.md`, or a dedicated docs page on `www.open-orchestrator.com`. It incorporates the best ideas from our previous discussion, the Claude Code leak patterns, and your existing foundation (git worktrees, Textual switchboard TUI, Conflict Guard, DAG planning, Agno intelligence layer, Context Bridge, etc.).

---

# Open Orchestrator – Product Specification

**Version:** 0.3 (Target Release: April/May 2026)  
**Project:** https://github.com/gitpcl/open-orchestrator  
**Website:** https://www.open-orchestrator.com  
**Status:** Draft → Ready for Implementation  

## 1. Vision & Value Proposition

Open Orchestrator turns multiple AI coding agents (Claude Code, OpenCode, Cursor, Aider, etc.) into a **coordinated, persistent development team** that works safely in parallel without stepping on each other.

**Core Insight (inspired by Claude Code architecture):**  
The real moat in agentic coding is not the LLM — it’s the **orchestration layer**: isolated execution environments, self-healing memory, proactive background agency, deferred tool loading, and safe coordination primitives.

**Positioning:**  
“The orchestration engine for AI agent teams — git worktrees + switchboard + Claude Code patterns, built for developers who want 5–20 agents working together without chaos.”

**Key Differentiators:**
- Native git worktree isolation (no shared filesystem races)
- Live Textual Switchboard TUI for monitoring & control
- Conflict Guard + Quality Gates
- 3-layer self-healing memory system (MEMORY.md + topic files + transcript grep)
- KAIROS-style proactive “Dream” mode
- Deferred ToolSearch meta-tool
- Subagent forking with cheap context inheritance

## 2. Target Users & Use Cases

**Primary Users:**
- Solo developers running 3–10 parallel agent sessions
- Small teams coordinating specialized agents (researcher, implementer, reviewer, tester)
- Power users of Claude Code / OpenCode who hit coordination limits
- Open-source maintainers managing large refactors across branches

**Core Use Cases:**
1. **Parallel Feature Development** — Spin up one worktree per ticket; agents work independently then propose merges.
2. **Complex Refactors** — Planner agent → multiple implementation sub-agents → critic/review agent.
3. **Long-running Projects** — Persistent memory + nightly auto-dream consolidation across weeks/months.
4. **Swarm Mode** — “owt swarm” launches a full coordinator + worker team inside one worktree.
5. **Background Agency** — KAIROS/Dream mode monitors repo, suggests improvements, reacts to GitHub events while laptop is closed.

## 3. High-Level Architecture

```
User → CLI Commands (owt)
          ↓
Switchboard TUI (Textual) + tmux sessions
          ↓
Git Worktree Manager ←→ Isolated Agent Sessions
          ↓
Agno Intelligence Layer (or external LLM)
          ↓
Tool Protocol Adapters + Deferred ToolSearch
          ↓
Memory System (3-layer)
          ↓
Conflict Guard + Critic Subagents + Quality Gates
          ↓
Compaction Engine + Prompt Cache Discipline
```

**Key Layers (Claude-inspired where beneficial):**
- **Execution Layer:** Git worktrees + tmux + per-session agent harness
- **Coordination Layer:** DAG planner, subagent forking, coordinator mode
- **Memory Layer:** Lightweight index (MEMORY.md) + on-demand topic files + grep-only transcripts
- **Safety Layer:** Default-deny permissions, critic pattern, denial tracking, Conflict Guard
- **Background Layer:** KAIROS/Dream daemon with heartbeat

## 4. Core Features & Improvements (v0.3)

### 4.1 Foundation (Already Strong – Polish)
- `owt new <name>` – Create isolated git worktree + tmux session
- `owt switchboard` – Live TUI overview of all sessions (status, logs, resource usage)
- `owt plan` – DAG-based task planning
- `owt note` / Context Bridge – Inject into CLAUDE.md-style project rules
- Conflict Guard – Detects cross-worktree risks before merge/ship

**Polish Items:**
- Add demo GIF/asciinema to README + website
- One-command example repo (`owt demo`)
- Full structured JSON output mode for all commands
- `owt doctor --fix` for orphaned worktrees/tmux sessions

### 4.2 Memory System (Highest Impact Addition)
Implement Claude’s 3-layer self-healing memory:

- **Layer 1 – Index:** `MEMORY.md` (always loaded, pointers only, ~150 chars/line)
- **Layer 2 – Topic Files:** On-demand knowledge (facts, decisions, architecture)
- **Layer 3 – Transcripts:** Never loaded fully; only `grep` for specific identifiers

**New Commands:**
- `owt memory add <fact>` – Stores intelligently (topic file + index update)
- `owt memory consolidate` – Manual trigger for auto-dream logic
- `owt memory search <query>`

**Background Feature:**
- `owt dream --enable` → KAIROS-style daemon
  - Runs after 24h inactivity or on GitHub webhook
  - Reviews all worktrees, dedupes, resolves contradictions, rewrites vague notes
  - Uses forked subagent with limited tools (“autoDream”)

### 4.3 Tool & Agent Enhancements
- **Deferred Tool Loading + ToolSearch Meta-Tool**
  - Agents see only one `ToolSearch` tool initially
  - On demand: fuzzy search → inject real tool schemas
  - Prevents token explosion with 20–200 tools

- **Subagent Forking**
  - Cheap spawn of research / synthesis / implementation sub-agents (leverage KV cache sharing where possible)
  - Fork-join pattern for parallel planning

- **Critic Pattern**
  - Before destructive actions (`owt ship`, merge, edit), run critic subagent
  - “Is this safe? Any cross-worktree conflicts or regressions?”

- **Compaction Strategies** (5 composable)
  - Snip (old messages), Microcompact (offload tool outputs), Reactive Compact (on context errors), etc.

### 4.4 Orchestration & Coordination
- `owt swarm <goal>` – Launches coordinator + multiple specialized workers
- ULTRAPLAN-style deep planning mode (optional longer thinking via flag)
- Parallel tool execution guidance in system prompts
- Prompt cache discipline (stable/dynamic boundary, cache-break vector tracking)

### 4.5 Safety & Resilience
- Default-deny permissions with pre-tool critic hooks
- Denial tracking (3 consecutive or 20 total → degrade to user confirmation)
- Quality Gates before any `ship` or merge
- Anti-distillation basics (optional fake tools / signatures)

## 5. CLI Command Roadmap

**Phase 1 (v0.3):**
- `owt dream [--enable|--disable|--status]`
- `owt memory ...`
- `owt swarm <goal>`
- `owt critic <action>`

**Phase 2:**
- `owt undercover` (strip internal codenames for OSS contributions)
- VS Code / Cursor extension for Switchboard integration
- Webhook support for GitHub events

## 6. Technical Implementation Notes

- **Language:** Python (uv + existing stack)
- **UI:** Textual (keep as primary TUI)
- **LLM Integration:** Flexible (Claude Code, OpenAI, local via Agno, etc.)
- **Prompt Engineering:** Re-inject project rules every turn (CLAUDE.md bridge)
- **Caching:** Track 10–14 cache-break vectors (model change, worktree count, memory delta, etc.)
- **Testing:** Add integration test suite with temporary repos

## 7. Non-Goals (v0.3)

- Full multi-machine orchestration
- Built-in model training or fine-tuning
- Replacing git itself
- Enterprise SSO / RBAC (future paid tier possible)

## 8. Success Metrics

- GitHub stars: 300+ within 30 days of v0.3
- Community: Active Discord + showcase discussions
- Adoption: At least 5 public “powered by Open Orchestrator” workflows
- Internal: Stable memory across 10+ worktrees for 7+ days

## 9. Inspiration & References

- Claude Code leaked architecture (March 2026): agent loop, 3-layer memory, KAIROS/Dream, deferred tools, compaction, critic pattern, subagent forking
- Existing Open Orchestrator foundation (git worktrees + Switchboard)
- Best practices from Aider, Continue.dev, Cursor Composer

---

**Next Steps Recommendation:**
1. Create `SPEC.md` in the repo root with this content.
2. Update README hero section to link to it.
3. Prioritize: Memory System → Deferred ToolSearch → KAIROS Dream (these deliver the biggest “wow”).
4. Add architecture diagram (Mermaid or Excalidraw) to both README and website.
