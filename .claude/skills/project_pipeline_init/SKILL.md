---
name: project-pipeline-init
description: Scan and initialize a MaaFramework game or app automation project for MaaMCP usage. Use when asked for project_pipeline_init, basic_info.md, researching a game repo, scanning pipeline nodes, finding common Back/Return/Exit/Confirm nodes, mapping pipeline node relationships, summarizing TemplateMatch image assets, OCR expected text conventions, resource groups, task entries, or reducing token cost for future Maa skill / MaaMCP work.
---

# project_pipeline_init

Use this skill to turn a MaaFramework consumer project into a compact onboarding document for future AI sessions. It scans the project's pipeline and image resources, identifies reusable control nodes, and writes `basic_info.md` at the target project root.

Do not run this skill against MaaMCP itself unless the user explicitly asks to analyze MaaMCP as a consumer project. MaaMCP is the framework; the normal target is a project such as `F:\workspace\MaaGumballs`.

## Core Workflow

1. Locate the target project root.
   - Prefer a user-provided path.
   - Otherwise look for `assets/interface.json`, then `interface.json`.
   - Treat `assets/interface.json` as the source of resource groups, controller types, task entries, and agent settings.

2. Run the analyzer in summary mode first:

   ```bash
   python .claude/skills/project_pipeline_init/scripts/analyze_pipeline_project.py F:\workspace\MaaGumballs
   ```

3. Inspect the summary for:
   - resource groups and task entries from `interface.json`
   - pipeline file count and unique node count
   - high in-degree common nodes
   - Back / Return / Exit / Close / Confirm / Wait / Flag nodes
   - unresolved references, isolated nodes, and cycle candidates
   - image directory inventory and TemplateMatch usage

4. Generate `basic_info.md` only after the summary looks reasonable:

   ```bash
   python .claude/skills/project_pipeline_init/scripts/analyze_pipeline_project.py F:\workspace\MaaGumballs --write-basic-info
   ```

   If `basic_info.md` already exists, the script refuses to overwrite it. Use `--overwrite` only after the user explicitly confirms.

5. Report where `basic_info.md` was written and name the most important sections that still need human review.

## What The Analyzer Reads

- `assets/interface.json` or `interface.json`
- all `assets/resource/**/pipeline/**/*.json`
- `default_pipeline.json` under resource roots
- all files under `assets/resource/**/image/**`

For MaaGumballs-style projects, the script should discover entries such as `Start_Up`, `DailyTask`, `Reward_Execute`, `Shop`, `AutoSky`, `JJC`, `Mars`, `DivineForgeLand_Start`, `TSD_Entry`, `AutoCdk`, and `StopGumballs`, then connect them to the pipeline nodes that define them.

## Relationship Rules

Parse these pipeline link fields:

- `next`
- `on_error`
- `interrupt`

Support these node reference forms:

- plain strings: `"ConfirmButton"`
- lists: `["A", "B"]`
- NodeAttr objects: `{ "name": "A", "jump_back": true }`
- prefixed strings: `"[JumpBack]BackText"`, `"[Anchor]SomeNode"`

Strip bracket prefixes when resolving the target node, but preserve the prefix in summaries where useful.

## Public Node Detection

Treat a node as likely reusable when either condition is true:

- it has high in-degree across the merged graph
- its name or behavior indicates a common UI operation

Important common categories:

- Back / Return / Exit / Close / Logout / Stop
- Confirm / Cancel / Retry
- Wait / Loading / Communicating / PowerLack
- Flag / Check / State probe
- `ClickKey` with Android key `4`
- shared TemplateMatch assets such as back buttons, return buttons, confirm buttons, settings buttons

## `basic_info.md` Contents

The generated document must be concise and useful to an AI agent. Include:

1. Project overview
2. Resource groups and task entries
3. Main pipeline inventory
4. Common public nodes
5. Back / Return / Exit / popup handling
6. Node relationship summary
7. OCR expected text conventions
8. TemplateMatch image inventory
9. Resolution and ROI conventions
10. Risks and TODOs

Keep automatically detected facts separate from TODOs. Do not invent game semantics that are not present in the project files.

## Optional Live MaaMCP Research

If a device or window is available and the user wants deeper game research, use MaaMCP after file scanning:

1. connect to the simulator/window
2. take a default `screencap`
3. infer portrait or landscape from image width/height
4. use OCR for visible text and key buttons
5. add stable UI facts to `basic_info.md`

This is an enhancement, not a blocker. File scanning must work without a live device.

## Do Not

- Do not overwrite an existing non-empty `basic_info.md` without explicit confirmation.
- Do not commit generated `basic_info.md` from another repository into MaaMCP.
- Do not modify unrelated target-project files while scanning.
- Do not treat OCR/image guesses as facts unless they came from files or live MaaMCP verification.
