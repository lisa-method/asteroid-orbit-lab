from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import prepare_selector_confirmation as p


class ConfirmationSampleTests(unittest.TestCase):
    def test_quota_denominators_and_timing(self):
        self.assertEqual(sum(p.QUOTAS.values()),100)
        self.assertEqual(sum(p.TIMING_QUOTAS.values()),24)
        self.assertEqual(set(p.QUOTAS),set(p.TIMING_QUOTAS))
        self.assertTrue(all(p.TIMING_QUOTAS[k]<=v for k,v in p.QUOTAS.items()))

    def test_provisional_designations_do_not_turn_into_paths(self):
        self.assertEqual(p.safe_id('2001 AV43'),'2001_AV43')
        for value in ('../foo','2001/A','bad;command','a\nb'):
            with self.assertRaises(ValueError): p.safe_id(value)

    def test_no_catalogue_truncation_or_numbered_only_earth_filter(self):
        from urllib.parse import parse_qs,urlsplit
        for name,url,_ in p.queries():
            q=parse_qs(urlsplit(url).query)
            self.assertNotIn('limit',q)
            if name=='Earth': self.assertEqual(q['kind'],['a'])

    def test_orbit_quality_does_not_use_target_error(self):
        doc={'object':{'kind':'au'},'orbit':{'condition_code':'2','data_arc':'900','two_body':False}}
        self.assertTrue(p.quality(doc)[0])
        for key,value in [('condition_code','4'),('data_arc','364'),('two_body',True)]:
            changed={'object':doc['object'],'orbit':{**doc['orbit'],key:value}}
            self.assertFalse(p.quality(changed)[0])
        self.assertFalse(p.quality({'orbit':{}})[0])

    def test_event_selection_is_unique_object_not_unique_row(self):
        fields=['des','dist','jd','cd']
        document={'fields':fields,'data':[
            ['2001 AV43','.002','2460000','2028-Feb-01 00:00'],
            ['2001 AV43','.003','2460001','2028-Feb-02 00:00'],
            ['99942','.0002','2460002','2028-Feb-03 00:00'],
            ['123','.02','2460003','2028-Feb-04 00:00']]}
        result=p.event_candidates(document,'Earth_tight',{'99942'})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['des'],'2001 AV43')
        self.assertEqual(result[0]['dist'],'.002')
        moderate=p.event_candidates(document,'Earth_moderate',{'99942'})
        self.assertEqual([x['des'] for x in moderate],['123'])

    def test_immutable_sample_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.json'
            p.immutable(path,{'id':'a'});p.immutable(path,{'id':'a'})
            with self.assertRaises(RuntimeError):p.immutable(path,{'id':'b'})

if __name__=='__main__':unittest.main()
