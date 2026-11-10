# Submission checklist — due 14 Nov 2026, 23:59 Irish time

Email **Monika.Rawal@exlservice.com** from a team member's **Trinity email**.
Subject: `EXL Hackathon | <Team name> | Challenge 03`

- [ ] **Solution deck** (≤ 10 slides): problem · approach · architecture · results · Responsible AI · next steps
- [ ] **Demo video** (≤ 3 min, link): the £500 story — triage → fail → fix → approve → wave 2 passes first time → dashboard
- [ ] **Code repository** link (GitHub) with README + setup steps (`python run.py demo` must work on a fresh clone)
- [ ] **Architecture diagram** (one page): `docs/architecture.png`
- [ ] **Responsible AI note** (one page): `docs/responsible_ai.md`
- [ ] **AI tools declared** in README ("AI tools used") + existing open-source code listed
- [ ] Public / synthetic data only — no personal data
- [ ] Team name and members filled in README

## Before recording the video
- [ ] `pytest -q` green
- [ ] Demo on real engines if possible (`LEGACY_MODE=sqlserver`, `NEW_MODE=spark`)
- [ ] LLM fixer on (`LLM_PROVIDER=openai|azure`) to show a plain-English explanation
- [ ] Dashboard shows fixer calls falling from wave 1 to wave 2

## Deck: suggested 10 slides
1. Title + one-line pitch
2. The problem (1,600 programs, undocumented, exact reconciliation required)
3. Our idea: a migration factory (reduce → translate → break → prove → approve → learn)
4. Architecture diagram
5. Triage: what not to migrate (and why nothing is lost)
6. The £500 bug (breaker + field-level reconciliation)
7. Learning across waves (fixer calls per wave chart)
8. Business impact + ROI (state assumptions)
9. Responsible AI (human gates, audit log, synthetic data, limitations)
10. Next steps (real SQL Server/Spark at scale, more patterns, access-log evidence, audit pack)
