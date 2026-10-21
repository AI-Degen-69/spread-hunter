# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Automatic labelling

Five labels are applied automatically when an issue is opened or edited, by
`.github/workflows/labeler.yml` running the `github/issue-labeler` action.

| Label           | Applied when the issue text contains                                       |
| --------------- | -------------------------------------------------------------------------- |
| `bug`           | bug, crash, traceback, regression, broken                                   |
| `documentation` | documentation, readme, doc(s), typo                                         |
| `enhancement`   | feature request, enhancement, would be nice                                  |
| `question`      | how do i, how can i, why does, why is, is there a way                       |
| `needs-triage`  | needs-triage                                                                |

The patterns live in `.github/labeler.yml`. The tracker's whole label set --
names, colours, descriptions -- is `.github/labels.yaml`, applied by
`.github/workflows/labels-sync.yaml` (additive: it never prunes a label made in
the UI). Both files are covered by `tests/test_issue_labeler.py`.

Three things to know before editing either:

- **A human's label is never taken away.** The action runs add-only
  (`sync-labels: 0`), so editing an issue never un-triages it.
- **Severity is not automatic.** Nothing applies `🚨 Critical`. That is a
  maintainer's judgement, and a keyword that fires on "urgent" labels ordinary
  frustration as a P0.
- **Patterns are case-insensitive and written as `/pattern/i`.** The action
  hands a bare string to `String.match`, which is case-sensitive. Several
  patterns under one label are ANDed rather than ORed -- every listed pattern
  has to match -- which is why each label here carries exactly one pattern.
