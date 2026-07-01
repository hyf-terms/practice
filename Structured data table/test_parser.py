import importlib.util, sys, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("parser", Path(__file__).with_name("parse_reduction_announcements.py"))
parser = importlib.util.module_from_spec(spec); sys.modules[spec.name] = parser; spec.loader.exec_module(parser)

SAMPLE = """
关于原持股 5%以上股东减持计划期限届满暨减持结果的公告
股东名称 GLP Renewable Energy Investment I Limited
股东身份 直接持股 5%以上股东 √是 □否
出于其集团战略与经营计划综合考虑，拟减持公司股份。
减持数量 44,157,600股
减持期间 2026 年 3 月 23 日～2026 年 5 月 28 日
减持比例 1.5160%
"""

class ParserTests(unittest.TestCase):
    def test_eligible(self): self.assertTrue(parser.eligible(SAMPLE))
    def test_negative_checkbox_not_eligible(self):
        text="关于股东减持计划公告 直接持股 5%以上股东 □是 √否"
        self.assertFalse(parser.eligible(text, "关于股东减持计划公告"))
    def test_name(self): self.assertIn("GLP Renewable Energy Investment I Limited", parser.shareholder_names(SAMPLE))
    def test_received_shareholder_name(self):
        text="公司于近日收到股东中通投资发来的《股份减持告知函》"
        self.assertIn("中通投资", parser.shareholder_names(text))
    def test_increase_excluded(self):
        text="持股5%以上股东。本次权益变动为信息披露义务人履行此前披露的增持计划。"
        self.assertFalse(parser.eligible(text, "关于持股比例触及1%整数倍的公告"))
    def test_shares(self): self.assertEqual(parser.reduction_shares(SAMPLE, "减持结果"), 44157600)
    def test_ratio(self): self.assertAlmostEqual(parser.reduction_ratio(SAMPLE, "减持结果"), .01516)
    def test_period(self): self.assertEqual(parser.reduction_period(SAMPLE, "减持结果"), "2026-03-23至2026-05-28")

if __name__ == "__main__": unittest.main()
