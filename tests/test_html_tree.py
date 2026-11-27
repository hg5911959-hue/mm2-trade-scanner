from __future__ import annotations

from test_support import IsolatedDataTestCase
from tools.html_tree import PageTree, has_trade_sides, node_text, smallest_matching_nodes, walk_nodes


class HtmlTreeTests(IsolatedDataTestCase):
    def test_tree_preserves_text_nested_nodes_and_image_alt_labels(self):
        tree = PageTree()
        tree.feed(
            "<main><div><span>Offering</span><span>Looking for</span>"
            '<img alt="Chroma Knife"></div></main>'
        )

        text = node_text(tree.root)
        self.assertIn("Offering", text)
        self.assertIn("Looking for", text)
        self.assertIn("Chroma Knife", text)
        self.assertTrue(has_trade_sides(text))
        self.assertEqual([node.tag for node in walk_nodes(tree.root)],
                         ["document", "main", "div", "span", "span", "img"])

    def test_smallest_matching_nodes_returns_the_deepest_matching_descendant(self):
        tree = PageTree()
        tree.feed("<main><div><span>Offering</span><span>Looking for</span></div></main>")

        matches = smallest_matching_nodes(tree.root, lambda text: "Offering" in text)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tag, "span")
        self.assertEqual(node_text(matches[0]), "Offering")

    def test_void_and_self_closing_tags_do_not_corrupt_the_stack(self):
        tree = PageTree()
        tree.feed('<div>Knife<img alt="Bat"/><br/><span>End</span></div>')

        self.assertIn("Knife Bat End", node_text(tree.root))
        self.assertEqual(tree.stack[-1].tag, "document")
