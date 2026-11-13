import unittest
from tools.alert_policy import rank_and_split
class AlertPolicyTests(unittest.TestCase):
    def test_twenty_qualifying_offers_split_fifteen_and_five(self):
        offers=[{"id":str(i),"difference":i,"margin":i} for i in range(20)]
        selected,deferred=rank_and_split(offers,15)
        self.assertEqual(len(selected),15)
        self.assertEqual(len(deferred),5)
        self.assertEqual(selected[0]["id"],"19")
        next_selected,next_deferred=rank_and_split(deferred,15)
        self.assertEqual(len(next_selected),5)
        self.assertEqual(next_deferred,[])
    def test_zero_means_unlimited(self):
        selected,deferred=rank_and_split([{"difference":1},{"difference":2}],0)
        self.assertEqual(len(selected),2)
        self.assertEqual(deferred,[])
if __name__=="__main__": unittest.main()
