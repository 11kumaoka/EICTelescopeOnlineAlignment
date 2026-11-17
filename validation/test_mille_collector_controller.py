"""Python orchestration checks for the optional iteration-local Mille Collector."""
import json
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest.mock import patch

from calibration.reconstruction import operations


def mille_binary(records):
    """Create parser-valid ten-row records without claiming physical content."""
    output = bytearray()
    for _ in range(records):
        values = [0.]
        labels = [0]
        for _row in range(10):
            values.extend((0., 1.))
            labels.extend((0, 0))
        words = -2 * len(values)
        output.extend(struct.pack('<i', words))
        output.extend(struct.pack('<' + 'd' * len(values), *values))
        output.extend(struct.pack('<' + 'i' * len(labels), *labels))
    return bytes(output)


class FakeCollector:
    def __init__(self, command, *, exit_code=0):
        arguments = list(map(str, command))
        self.values = {arguments[index]: arguments[index + 1]
                       for index in range(1, len(arguments), 2)}
        self.pid = 43210
        self.returncode = None
        self.exit_code = exit_code
        geometry_id = self.values['--geometry-id']
        expected = int(self.values['--records'])
        ready = {
            'schema': operations.MILLE_COLLECTOR_READY_SCHEMA,
            'endpoint': 'tcp://127.0.0.1:43123',
            'pid': self.pid,
            'expected_records': expected,
            'model_id': self.values['--model-id'],
            'configuration_id': self.values['--configuration-id'],
            'geometry_id': geometry_id,
            'output': self.values['--output'],
        }
        Path(self.values['--ready']).write_text(json.dumps(ready))

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        self.returncode = self.exit_code
        if self.returncode == 0:
            expected = int(self.values['--records'])
            output = Path(self.values['--output'])
            payload = mille_binary(expected)
            output.write_bytes(payload)
            report = {
                'schema': operations.MILLE_COLLECTOR_REPORT_SCHEMA,
                'passed': True,
                'protocol_version': 3,
                'stream_id': int(self.values['--geometry-id'][:16], 16),
                'records_received': expected,
                'records_written': expected,
                'expected_records': expected,
                'eos_sequence': expected,
                'payload_bytes': len(payload),
                'output_bytes': len(payload),
                'scalar_measurements': 10 * expected,
                'model_id': self.values['--model-id'],
                'configuration_id': self.values['--configuration-id'],
                'geometry_id': self.values['--geometry-id'],
                'partial_path': str(output) + '.partial',
                'final_path': str(output),
                'published': True,
                'error': None,
                'events': [{'sequence': sequence, 'run_number': 7,
                            'event_number': sequence, 'scalar_measurements': 10}
                           for sequence in range(expected)],
            }
            Path(self.values['--report']).write_text(json.dumps(report))
        return self.returncode


class MilleCollectorOrchestration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mille-collector-python-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.build = self.root / 'build'
        self.build.mkdir()
        self.source = self.root / 'channels.root'
        self.source.write_bytes(b'SIMULATED RAW INPUT')
        self.geometry = self.root / 'geometry.xml'
        self.geometry.write_text('<geometry/>')
        self.config = self.root / 'alignment.json'
        self.config.write_text('{}')
        self.events = 5
        self.commands = []

    def execute(self, command, directory, **_arguments):
        command = list(map(str, command))
        self.commands.append(command)
        if command[0] != 'eicrecon':
            Path(command[-1]).write_text(json.dumps({
                'passed': True, 'model_sha256': 'a' * 64}))
            return
        (directory / 'audit.json').write_text(json.dumps({
            'processed': self.events, 'accepted': self.events,
            'rejected': 0, 'events': [
                {'run_number': 7, 'event_number': event}
                for event in range(self.events)]}))
        (directory / 'mille.json').write_text(json.dumps({
            'passed': True, 'processed': self.events, 'accepted': self.events,
            'rejected': 0, 'records': self.events, 'events': []}))
        (directory / 'reco.root').write_bytes(b'SIMULATED RECONSTRUCTION')

    def reconstruct(self, transport):
        output = self.root / ('reconstruct-' + transport)
        values = [(event, [(layer, 1, 0) for layer in range(5)])
                  for event in range(self.events)]
        with patch.object(operations, 'execute', side_effect=self.execute), \
                patch.object(operations, 'environment', return_value={}), \
                patch.object(operations, 'channel_values', return_value=values), \
                patch.dict('sys.modules', {'numpy': types.ModuleType('numpy')}):
            operations.reconstruct(self.build, output, self.geometry, self.source,
                self.config, self.events, mille_transport=transport)
        return output

    def test_file_mode_retains_the_existing_eicrecon_parameters(self):
        with patch.object(operations.subprocess, 'Popen',
                          side_effect=AssertionError('Collector must not start')):
            output = self.reconstruct('file')
        command = next(value for value in self.commands if value[0] == 'eicrecon')
        self.assertIn(f'-PTelescopeMille:output={output}/mille.dat', command)
        self.assertFalse(any('TelescopeMille:outputMode=' in value for value in command))
        self.assertTrue((output / 'complete.json').is_file())

    def test_zeromq_mode_waits_for_ready_and_exact_collector_completion(self):
        collector = self.build / 'TelescopeMilleCollector'
        collector.write_bytes(b'SIMULATED EXECUTABLE')
        processes = []

        def start(command, **_arguments):
            process = FakeCollector(command)
            processes.append(process)
            return process

        with patch.object(operations.subprocess, 'Popen', side_effect=start):
            output = self.reconstruct('zeromq')
        self.assertEqual(len(processes), 1)
        command = next(value for value in self.commands if value[0] == 'eicrecon')
        self.assertIn('-PTelescopeMille:outputMode=zeromq', command)
        self.assertIn('-PTelescopeMille:endpoint=tcp://127.0.0.1:43123', command)
        self.assertIn('-PTelescopeMille:timeoutMs=30000', command)
        self.assertFalse(any(value.startswith('-PTelescopeMille:output=') for value in command))
        self.assertEqual((output / 'mille.dat').read_bytes(), mille_binary(self.events))
        self.assertTrue((output / 'mille-collector.json').is_file())
        self.assertTrue((output / 'complete.json').is_file())

    def test_nonzero_collector_exit_never_completes_reconstruction(self):
        collector = self.build / 'TelescopeMilleCollector'
        collector.write_bytes(b'SIMULATED EXECUTABLE')
        with patch.object(operations.subprocess, 'Popen',
                          side_effect=lambda command, **_: FakeCollector(command, exit_code=7)), \
                self.assertRaisesRegex(RuntimeError, 'exited with code 7'):
            self.reconstruct('zeromq')
        self.assertFalse((self.root / 'reconstruct-zeromq/complete.json').exists())


if __name__ == '__main__':
    unittest.main()
