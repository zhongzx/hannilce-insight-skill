# Hannilce Insight

> *Inspired by the interrogation room in* **The Silence of the Lambs** *— two people, separated by glass, dismantling each other's defenses.*
>
> **Hannilce** = Hannibal + Clarice, head-to-tail, like a secret code.

---

## What Is It

**Hannilce Insight** is an MBTI personality profiling skill. Unlike traditional quizzes, it reads your personality through **real conversation** — not a questionnaire.

As you discuss news topics, express opinions, and share reactions, the system maps your preferences across four MBTI dimensions:

| Dimension | What It Reads |
|-----------|---------------|
| **E / I** | Extraversion vs Introversion — how you recharge |
| **S / N** | Sensing vs Intuition — how you process information |
| **T / F** | Thinking vs Feeling — how you make decisions |
| **J / P** | Judging vs Perceiving — how you organize life |

---

## How to Use

```
/MBTI <Name>
```

Example:

```
/MBTI Alice
/MBTI Bob
```

- Each person gets an independent analysis session
- Resuming a previous session picks up right where it left off (awakening mechanism)

---

## How It Works

```
Topic Generation  →  User Response  →  Quality Scoring  →  Dimension Mapping
    (news/AI)          (raw text)         (token+semantic)     (E/I S/N T/F J/P)
```

### Dynamic Topics

- **Primary**: Scrapes hot topics from Chinese news sites daily (Huxiu, Caixin, 36Kr, Toutiao, Babytree)
- **Fallback**: Built-in pool of 10 structured conversation starters across all 4 MBTI dimensions

### Quality Control

- **Token layer**: Monitors response length (100–500 tokens/round is healthy)
- **Semantic layer**: Separate LLM scores richness, opinion clarity, and emotional sincerity
- **Archive trigger**: Auto-archives a topic if quality drops continuously across rounds

### Confidence Model

```
Confidence = Token Factor × Quality Factor × Consistency Factor
```

Analysis ends when: rounds ≥ 8 **AND** confidence ≥ threshold **AND** all 4 dimensions covered.

---

## Output Report

```
【 Hannilce Insight · MBTI Profile 】

🔍 User: Zhang San
📊 Confidence: 85%

── Four Dimensions ──
E/I ████████████░░░░░░░ 7:3 → Extroverted (ENTP tendency)
S/N ███████░░░░░░░░░░░░ 3:7 → Intuitive
T/F ██████████████░░░░░ 8:2 → Thinking
J/P ████████████░░░░░░░ 6:4 → Judging

🎯 MBTI Type: ENTJ

── Core Traits ──
[Personalized description based on conversation content]

💬 Typical Expression Style:
• [Extracted from conversation patterns]

📰 News Topic Preferences:
• [Reveals underlying values]
```

---

## Project Structure

```
mbti/
├── skill/
│   └── SKILL.md              # Skill entry (aily system registration)
├── references/
│   ├── db_schema.md          # SQLite table definitions
│   ├── topic_pool.md         # 10 built-in fallback topics
│   └── news_source_map.md    # News source configuration
├── seed_data/
│   ├── *.png                 # News source verification screenshots
│   └── news_source_verification_report.md
└── README.md                 # This file
```

**Database**: SQLite WAL mode → `~/.aily/workspace/mbti/mbti.db`

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Trigger: /MBTI <Name>                                  │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌────────────┐  │
│  │ Topic Engine│ →  │  Dialogue   │ →  │  Scorer    │  │
│  │ (news/AI)   │    │  Store     │    │(token/sem) │  │
│  └─────────────┘    └─────────────┘    └────────────┘  │
│                           ↓                              │
│                    ┌─────────────┐                       │
│                    │  MBTI Report│                       │
│                    │  Generator  │                       │
│                    └─────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

---

## License

MIT — for educational and personal use.
