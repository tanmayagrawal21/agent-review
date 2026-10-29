from agent_review.privacy import redact, scan_added

DIFF = """diff --git a/notes.md b/notes.md
new file mode 100644
--- /dev/null
+++ b/notes.md
@@ -0,0 +1,2 @@
+hello
+call Jane Doe at (555) 010-4477
diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -3,2 +3,2 @@
 keep
-jane doe was here
+fine
"""


def test_added_lines_only_with_new_side_line_numbers_and_phone_separators():
    hits = scan_added(DIFF, ["Jane Doe", "555.010.4477"])
    assert [(h.file, h.line, h.term) for h in hits] == [("notes.md", 2, 1), ("notes.md", 2, 2)]


def test_redact():
    assert redact("Ask JANE DOE on 555-010-4477.", ["jane doe", "(555) 010 4477"]) == "Ask [redacted] on [redacted]."
