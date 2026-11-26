import hashlib
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from r031_evidence.atp_content_relations import _index,_body_paragraphs,_citation,extract_relations


class Fragments(unittest.TestCase):
    def test_embedded_figure_is_not_reason_to_drop_author_prose(self):
        root=ET.fromstring('''<article><body><sec><title>Results</title><p>Hesperetin 10 µM increased ATP (Fig. 2b).
        <fig id="Fig2"><caption><p>HepG2 cells, 50 µM, 48 h.</p></caption></fig>
        Other author text after the figure.</p></sec></body></article>''')
        index=_index(root);blocks=_body_paragraphs(index)
        self.assertEqual([b['text'] for b in blocks],['Hesperetin 10 µM increased ATP (Fig. 2b).','Other author text after the figure.'])
        for b in blocks:
            c=_citation(b,'0'*64);self.assertEqual(index[c['source_anchor']]['text'][c['start']:c['end']],c['quote'])
            self.assertNotIn('50 µM',b['text'])

    def test_repeated_tail_text_uses_actual_tail_offset_not_caption_occurrence(self):
        root=ET.fromstring('''<article><body><sec><title>Methods</title><p>Start.
        <fig><caption><p>ATP was measured.</p></caption></fig> ATP was measured.</p></sec></body></article>''')
        index=_index(root);blocks=_body_paragraphs(index);tail=blocks[-1];citation=_citation(tail,'0'*64)
        full=index[citation['source_anchor']]['text']
        self.assertEqual(citation['start'],full.rindex('ATP was measured.'))

    def test_nested_table_notes_cannot_supply_a_methods_paragraph(self):
        root=ET.fromstring('''<article><body><sec><title>Methods</title><p>Actual method prefix.
        <table-wrap><caption><p>ATP assay 25 µM for 48 h.</p></caption></table-wrap> End.</p></sec></body></article>''')
        self.assertEqual([b['text'] for b in _body_paragraphs(_index(root))],['Actual method prefix.','End.'])

    def test_inline_marks_preserve_source_offsets_and_no_invented_whitespace(self):
        root=ET.fromstring('''<article><body><sec><title>Results</title><p>ATP<italic>content</italic><fig><caption><p>cap</p></caption></fig>after <bold>24 h</bold>.</p></sec></body></article>''')
        index=_index(root);blocks=_body_paragraphs(index)
        self.assertEqual([b['text'] for b in blocks],['ATPcontent','after 24 h.'])
        for b in blocks:
            c=_citation(b,'0'*64);self.assertEqual(index[c['source_anchor']]['text'][c['start']:c['end']],b['text'])
