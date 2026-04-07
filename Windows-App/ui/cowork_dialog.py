"""Cowork setup prompt dialog — copies a Cowork automation prompt to clipboard."""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QApplication,
)

COWORK_PROMPT = """\
Set up a scheduled project that monitors my HiDock transcripts and automatically generates summaries using the correct template.

## Folders
- Transcriptions: ~/HiDock/Transcriptions/
- Summary Templates: ~/HiDock/Summary Templates/
- Summaries output: ~/HiDock/Summaries/

## Processing Rules

### 1. Check readiness
For each transcript in ~/HiDock/Transcriptions/, find the matching _diarized.json file. \
Only process transcripts where ALL speakers have been named (no "Speaker 0", "Speaker 1" \
etc. remaining in speaker_names). Skip any transcript that already has a matching summary \
in ~/HiDock/Summaries/.

### 2. Assess meeting type
Read the transcript content and determine the meeting type by analysing:
- Number and roles of participants
- Discussion topics and tone
- Meeting structure (formal vs informal, status update vs deep-dive)

### 3. Select template
Pick the best matching template from ~/HiDock/Summary Templates/:
- "1 on 1 Meeting" — two participants, informal catch-up or coaching
- "Client or External Meeting" — mixed internal/external attendees
- "Job Interview" — candidate + interviewer dynamic
- "Project Sync" — technical/delivery focused, sprint or milestone review
- "Stand Up Meeting" — short, status-update format
- "Brainstorming" — ideation, open-ended exploration
- "Podcast" — interview/conversation format for publication
- "Retrospective Meeting" — what went well / what to improve
- "Weekly Team Meeting" — recurring team sync with multiple topics
- "Project kick-off" — new initiative, roles and milestones
- "Training or Workshop" — learning/teaching session
- "General Meeting" — fallback if no clear match

### 4. Generate summary
Apply the selected template to the transcript, following all extraction guidance \
within the template. Output to ~/HiDock/Summaries/ with filename format:
YYYY-MM-DD - {Template Name} - {Area} - {Short Description}.md

### 5. Obsidian integration
After generating the summary, copy it into the Obsidian vault with this frontmatter prepended:
---
type: meeting
date: YYYY-MM-DD
template: {template name used}
area: {extracted area from template}
participants:
  - "[[Participant Name]]"
tags: [meeting, {area-slug}, {template-slug}]
source: {original transcript filename}
---

Then run: obsidian open --path "{note path}"
"""


class CoworkDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cowork Setup")
        self.setMinimumSize(640, 520)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel("\u2728 Cowork Setup Prompt")
        header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(header)

        desc = QLabel(
            "Copy this prompt and paste it into Claude Cowork to set up "
            "automated transcript summarisation."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #a6adc8;")
        layout.addWidget(desc)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlainText(COWORK_PROMPT)
        self._text.setStyleSheet(
            "font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px;"
        )
        layout.addWidget(self._text, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._copy_btn = QPushButton("Copy to Clipboard")
        self._copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self._copy_btn)
        layout.addLayout(btn_row)

    def _copy(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(COWORK_PROMPT)
        self._copy_btn.setText("\u2713 Copied!")
        QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy to Clipboard"))
