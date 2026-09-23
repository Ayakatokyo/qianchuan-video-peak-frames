# Qianchuan Skill Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static skill introduction page and a desensitized final report sample.

**Architecture:** The showcase is two standalone HTML files under `showcase/`. `index.html` explains the skill and links to `desensitized-final-report.html`; the report sample mirrors the six final-report business sections using anonymous data and CSS placeholders instead of real images.

**Tech Stack:** HTML5 and embedded CSS only. No JavaScript, package installation, or runtime dependency is required.

---

## File Structure

- Create: `showcase/index.html`
  - Static introduction page for the `千川短视频素材分析` skill.
  - Contains value proposition, workflow, contracts, deliverables, and a link to the report sample.
- Create: `showcase/desensitized-final-report.html`
  - Static anonymized final-report sample.
  - Contains the six required business sections and anonymous material cards.

### Task 1: Create The Skill Introduction Page

**Files:**
- Create: `showcase/index.html`

- [ ] **Step 1: Add the standalone HTML page**

Create `showcase/index.html` with these sections:

- Hero: `千川短视频素材分析技能`
- Use cases: high-spend material review, creative pattern extraction, budget decision support
- Inputs: analysis period, Top material count, advertiser id, RPA shop id, runtime credentials
- Workflow: Top candidate export, streaming RPA diagnosis, peak-frame extraction, verification, diagnostic fact pack, AI analysis, final report
- Contracts: T+2 date conversion, candidate pool versus target Top N, `top_materials_rpa_ready.json`, no execution-process narrative
- Deliverables: JSON, CSV, JPEG, summaries, diagnostic data, final HTML
- Link: `desensitized-final-report.html`

- [ ] **Step 2: Verify introduction content**

Run:

```bash
test -f showcase/index.html
rg -n "千川短视频素材分析技能|T\\+2|top_materials_rpa_ready\\.json|desensitized-final-report\\.html" showcase/index.html
```

Expected: command exits `0` and prints matching lines.

### Task 2: Create The Desensitized Final Report

**Files:**
- Create: `showcase/desensitized-final-report.html`

- [ ] **Step 1: Add the standalone HTML report**

Create `showcase/desensitized-final-report.html` with these exact six section headings:

- `01｜TOP 素材组合结论`
- `02｜人群洞察`
- `03｜脚本洞察`
- `04｜高光帧洞察`
- `05｜单素材深度分析`
- `06｜下一轮策略`

Use anonymous identifiers only:

- `某滋补食品品牌`
- `ADV-XXXXXX`
- `SHOP-XXXXXX`
- `MAT-A001`
- `MAT-A002`
- `MAT-A003`

Use CSS frame placeholders instead of image files.

- [ ] **Step 2: Verify report structure**

Run:

```bash
rg -n "01｜TOP 素材组合结论|02｜人群洞察|03｜脚本洞察|04｜高光帧洞察|05｜单素材深度分析|06｜下一轮策略" showcase/desensitized-final-report.html
```

Expected: command exits `0` and prints all six section headings.

### Task 3: Run Static And Desensitization Checks

**Files:**
- Verify: `showcase/index.html`
- Verify: `showcase/desensitized-final-report.html`

- [ ] **Step 1: Check no obvious sensitive strings were copied into showcase files**

Run:

```bash
rg -n "小仙炖|/Users/shuzida|https?://|7647499280192372763|ENV_BACKEND_HOST|YUCE_AUTHORIZATION|YUCE_SESSION_ID" showcase
```

Expected: command exits `1` with no matches.

- [ ] **Step 2: Check cross-links and required files**

Run:

```bash
test -f showcase/index.html && test -f showcase/desensitized-final-report.html && rg -n "desensitized-final-report\\.html|index\\.html" showcase
```

Expected: command exits `0` and prints cross-link lines.

## Self-Review

This plan covers every requirement in the approved spec: two static files, the operator-console introduction style, six-section report sample, strict anonymization, and lightweight local verification. It contains no runtime changes and no placeholders requiring later interpretation.
