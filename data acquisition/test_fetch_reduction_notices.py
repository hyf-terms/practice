import importlib.util, sys, unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("fetch_reduction_notices",Path(__file__).with_name("fetch_reduction_notices.py")); module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)

class Tests(unittest.TestCase):
    def test_high(self):
        r=module.fuzzy_match("关于持股5%以上股东减持股份计划的公告",["股东减持"]); self.assertTrue(r.candidate); self.assertEqual(r.level,"high")
    def test_medium(self):
        r=module.fuzzy_match("关于控股股东减持计划实施进展的公告",[]); self.assertTrue(r.candidate); self.assertEqual(r.level,"medium")
    def test_low(self):
        r=module.fuzzy_match("关于股东股份减持结果的公告",[]); self.assertTrue(r.candidate); self.assertEqual(r.level,"low")
    def test_excluded(self):
        self.assertFalse(module.fuzzy_match("限制性股票激励计划归属结果公告",["股权激励"]).candidate)
    def test_management_only_excluded_even_if_category_is_broad(self):
        r=module.fuzzy_match("董事和高级管理人员减持股份结果公告",["大股东减持"]); self.assertFalse(r.candidate)

if __name__ == "__main__": unittest.main()
