"""The review prompt binds its verdict to the run the resolver looks for.

Cortex ADR-1130: the resolver reads a verdict only from a comment that links
a successful review run of the head. A review posted with `gh pr comment`
carried the verdict and no link, so an approved pull request stayed unmerged.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "claude-review.yml"
VERDICT = 'End with exactly one line: "Verdict: approve" or "Verdict: block".'
LINK = (
    '"[View job run](${{ github.server_url }}/${{ github.repository }}'
    '/actions/runs/${{ github.run_id }})"'
)


class ReviewPromptTests(unittest.TestCase):
    def test_prompt_puts_the_run_link_in_every_comment_that_carries_the_verdict(self):
        prompt = WORKFLOW.read_text()
        self.assertEqual(prompt.count(VERDICT), 1)
        self.assertEqual(prompt.count(LINK), 1)
        self.assertIn("in every comment that carries the verdict line", prompt)
        self.assertLess(prompt.index(VERDICT), prompt.index(LINK))


if __name__ == "__main__":
    unittest.main()
