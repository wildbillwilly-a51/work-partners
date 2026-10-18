# Work partners

Read README.md, NOTICE and the directly relevant source before changing files.
The common skill is skills/work-partners/SKILL.md. The installer and tests are
under scripts/ and tests/. The lead owns edits; peers stay read-only.

Preserve native safety profiles, strict consultation validation, account limits,
shared cooldowns, explicit retry gates and the four-call task cap. Do not weaken
permissions or disguise requests to obtain a peer response.

Run Python 3.12+ offline tests with:
python -B -m unittest discover -s tests -p test_desktop_partners.py

Live calls require authorization and available account quota. Keep requests,
receipts, transcripts and sign-ins outside Git. Respect the source-available
usage and redistribution terms in NOTICE.
