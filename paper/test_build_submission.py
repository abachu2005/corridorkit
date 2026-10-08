"""Regression checks for manuscript typesetting helpers."""
import unittest

from build_submission import convert_table, inline, latex_escape, markdown_to_latex
from check_manuscript import words


class SubmissionTests(unittest.TestCase):
    def test_escape_does_not_reescape_inserted_commands(self):
        self.assertEqual(latex_escape("\\{}_%"), r"\textbackslash{}\{\}\_\%")

    def test_hyphens_survive_prose_and_links(self):
        self.assertEqual(inline("surgeon-supervised"), "surgeon-supervised")
        url = "https://github.com/abachu2005/corridorkit/issues"
        self.assertIn(url, inline(f"[GitHub issue tracker]({url})"))
        self.assertIn(r"\nolinkurl{github.com/a/skullbase-corridor/issues}",
                      inline("[github.com/a/skullbase-corridor/issues](https://github.com/a/skullbase-corridor/issues)"))

    def test_smart_quotes_use_tex_glyphs(self):
        self.assertEqual(inline("“not reached”"), "``not reached''")

    def test_metadata_has_explicit_column_and_row_spacing(self):
        output = convert_table([
            "| Field | Value |", "|---|---|",
            "| C4 Legal Code License | Apache License 2.0 |",
        ])
        self.assertIn(r"\hspace{0.05\linewidth}", output)
        self.assertIn(r"\raggedright\arraybackslash", output)
        self.assertIn(r"C4 Legal Code License & Apache License 2.0", output)
        self.assertIn(r"\addlinespace[4pt]", output)

    def test_citations_and_commands_survive_conversion(self):
        output = markdown_to_latex(
            "## 1. Motivation\n\nCorridorKit [@slicer; @nnunet].\n\n"
            "```bash\ncorridorkit doctor\n```\n"
        )
        self.assertIn(r"\section{Motivation}", output)
        self.assertIn(r"\citep{slicer,nnunet}", output)
        self.assertIn("corridorkit doctor", output)

    def test_word_count_ignores_nonprose(self):
        self.assertEqual(words(
            "## Heading\nA finite-instrument model [@slicer].\n"
            "```bash\none command\n```\n"
            "<p>Figure caption.</p>\n"
        ), 6)


if __name__ == "__main__":
    unittest.main()
