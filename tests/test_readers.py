from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python_origin_plotter'))
import origin_ui_bridge as bridge
import pandas as pd


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pdf_uses_intensity_not_spacing(self):
        path = self.directory / 'reference.txt'
        path.write_text('PDF#example\n2-Theta d(A) I(f) ( h k l)\n18.694 4.7427 100 (0 0 1)\n', encoding='utf-8')
        data = bridge.read_numeric_source(path, 2)
        self.assertEqual(data['values'][0].iloc[0], 18.694)
        self.assertEqual(data['values'][1].iloc[0], 100)

    def test_rigaku_header_length_is_variable(self):
        path = self.directory / 'sample.txt'
        path.write_text('*FILE_TYPE "RAS_RAW"\n# arbitrary header\n*MEAS_DATA_COUNT "2"\n10 100\n10.02 200\n', encoding='utf-8')
        self.assertEqual(bridge.read_numeric_source(path, 2)['rows'], 2)
        path.write_text('*FILE_TYPE "RAS_RAW"\n*MEAS_DATA_COUNT "3"\n10 100\n', encoding='utf-8')
        with self.assertRaises(bridge.plotter.PlotterError):
            bridge.read_numeric_source(path, 2)

    def test_battery_signs_mass_units_and_no_rest_boundaries(self):
        rows = []
        for cycle in (1, 2, 3):
            rows.extend([[cycle, 0, 3, 0, 0], [cycle, 1, 4, 0.001, 0],
                         [cycle, 0, 4.1, 0.001, 0], [cycle, -1, 3.1, 0.001, 0.0005],
                         [cycle, 0, 3.2, 0.001, 0.0005]])
        source = self.directory / 'cell.csv'
        pd.DataFrame(rows, columns=['Cycle_Index','Current','Voltage','Charge_Capacity','Discharge_Capacity']).to_csv(source, index=False)
        job = {'template':'CHARGE_AND_DISCHARGE','slots':[{'id':'battery_file','data':str(source)}],
               'electrode_weight_mg':10, 'foil_weight_mg':7.4, 'capacity_unit':'Ah'}
        config = bridge.plotter.load_config(ROOT / 'python_origin_plotter' / 'template_configs.json')
        output = self.directory / 'prepared.csv'
        _, _, _, details = bridge.prepare_slot_input(job, config, output)
        values = pd.read_csv(output)
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values.A.iloc[0], 0.001*1000/0.00208)
        self.assertEqual(values.B.iloc[0], 4)
        self.assertAlmostEqual(values.C.iloc[0], 0.0005*1000/0.00208)
        self.assertEqual(values.D.iloc[0], 3.1)
        self.assertEqual(details[0]['sample_name'], 'cell')
        job['foil_weight_mg'] = 10
        with self.assertRaises(bridge.plotter.PlotterError):
            bridge.prepare_slot_input(job, config, output)

    def test_sample_filename_default_and_manual_override(self):
        source = self.directory / 'Sample One.csv'
        source.write_text('X,Y\n1,2\n2,3\n', encoding='utf-8')
        config = bridge.plotter.load_config(ROOT / 'python_origin_plotter' / 'template_configs.json')
        slots = [{'id':s['id'], 'data':str(source)} for s in bridge.template_config('XRD1',config)['ui_slots']]
        job = {'template':'XRD1','slots':slots}
        _,_,_,details = bridge.prepare_slot_input(job,config,self.directory/'prepared.csv')
        self.assertTrue(all(d['sample_name']=='Sample One' for d in details))
        slots[0]['sample_name']='Custom'
        _,_,_,details = bridge.prepare_slot_input(job,config,self.directory/'prepared.csv')
        self.assertEqual(details[0]['sample_name'],'Custom')


if __name__ == '__main__':
    unittest.main()
