# Calibration layout migration

Baseline `main`: `70f9ecbba8dfd766d101f79114e6adc20a98178c`.
Feature branch: `refactor/calibration-layout`. Scientific algorithms, versioned schemas, C++ interfaces and historical artifacts are preserved.

## File migration

| Previous path | New path |
|---|---|
| `alignment/pede.py` | `calibration/alignment/pede.py` |
| `calibration/config/native_workflow_example.json` | `calibration/config/workflow_example.json` |
| `calibration/config/native_workflow_multi.json` | `calibration/config/workflow_multi.json` |
| `calibration/config/native_workflow_smoke.json` | `calibration/config/workflow_smoke.json` |
| `calibration/native_controller.py` | `calibration/controller.py` |
| `calibration/native_policy.py` | `calibration/policy.py` |
| `digitization/native.py` | `simulation/digitization.py` |
| `geometry/native_safety.py` | `geometry/safety.py` |
| `simulation/native.py` | `simulation/geant4.py` |
| `validation/telescope_alignment/TelescopeMilleDerivatives.cc` | `localDetEICrecon/experimental/telescope/tools/TelescopeMilleDerivatives.cc` |
| `validation/telescope_alignment/l1_global_rx.json` | `calibration/alignment/config/l1_global_rx.json` |
| `validation/telescope_alignment/l1_global_rx_ry_rz.json` | `calibration/alignment/config/l1_global_rx_ry_rz.json` |
| `validation/telescope_alignment/l1_global_ry.json` | `calibration/alignment/config/l1_global_ry.json` |
| `validation/telescope_alignment/l1_global_ry_rz.json` | `calibration/alignment/config/l1_global_ry_rz.json` |
| `validation/telescope_alignment/l1_global_rz.json` | `calibration/alignment/config/l1_global_rz.json` |
| `validation/telescope_alignment/l1_global_y.json` | `calibration/alignment/config/l1_global_y.json` |
| `validation/telescope_alignment/l1_global_y_rx_ry_rz.json` | `calibration/alignment/config/l1_global_y_rx_ry_rz.json` |
| `validation/telescope_alignment/l1_l2_global_y.json` | `calibration/alignment/config/l1_l2_global_y.json` |
| `validation/telescope_alignment/l1_l2_l3_y_rx_ry_rz.json` | `calibration/alignment/config/l1_l2_l3_y_rx_ry_rz.json` |
| `validation/telescope_alignment/l1_l2_y_rx_ry_rz.json` | `calibration/alignment/config/l1_l2_y_rx_ry_rz.json` |
| `validation/telescope_alignment/l1_l2_y_rx_ry_rz_l3_y.json` | `calibration/alignment/config/l1_l2_y_rx_ry_rz_l3_y.json` |
| `validation/telescope_alignment/l1_y_rx_l2_y.json` | `calibration/alignment/config/l1_y_rx_l2_y.json` |
| `validation/telescope_alignment/l1_y_rx_ry_rz_l2_y.json` | `calibration/alignment/config/l1_y_rx_ry_rz_l2_y.json` |
| `validation/telescope_closure/TelescopeChannels.cc` | `localDetEICrecon/experimental/telescope/tools/TelescopeChannels.cc` |
| `validation/telescope_closure/multi_information.py` | `calibration/alignment/information.py` |
| `validation/telescope_closure/observability.py` | `calibration/alignment/observability.py` |
| `validation/telescope_factories/TelescopeNavigationAudit.cc` | `localDetEICrecon/experimental/telescope/tools/TelescopeNavigationAudit.cc` |
| `validation/telescope_factories/TelescopeSerializationAudit.cc` | `localDetEICrecon/experimental/telescope/tools/TelescopeSerializationAudit.cc` |
| `validation/telescope_geometry/BarrelMetadata.cc` | `localDetEICrecon/experimental/telescope/geometry/BarrelMetadata.cc` |
| `validation/telescope_geometry/TelescopePhysicalGeometryAudit.cc` | `localDetEICrecon/experimental/telescope/tools/TelescopePhysicalGeometryAudit.cc` |
| `validation/telescope_geometry/prepare.py` | `geometry/prepare.py` |
| `validation/test_native_bias_diagnostics.py` | `validation/test_bias_diagnostics.py` |
| `validation/test_native_containment.py` | `validation/test_containment.py` |
| `validation/test_native_controller.py` | `validation/test_calibration_controller.py` |
| `validation/test_native_convergence.py` | `validation/test_convergence.py` |
| `validation/test_native_diagnostic.py` | `validation/test_diagnostic.py` |
| `validation/test_native_digitization.py` | `validation/test_digitization.py` |
| `validation/test_native_geant4.py` | `validation/test_geant4.py` |
| `validation/test_native_geometry_safety.py` | `validation/test_geometry_safety.py` |
| `validation/test_native_multi_sensor.py` | `validation/test_multi_sensor.py` |
| `validation/test_native_operations.py` | `validation/test_geometry_correction.py` |
| `validation/test_native_pede_covariance.py` | `validation/test_pede_covariance.py` |
| `validation/test_native_rotation_scope.py` | `validation/test_rotation_scope.py` |
| `validation/test_native_serialization.py` | `validation/test_serialization.py` |
| `validation/test_native_single_y.py` | `validation/test_single_y.py` |
| `validation/test_native_source_identity.py` | `validation/test_source_identity.py` |
| `validation/test_native_three_sensor.py` | `validation/test_three_sensor.py` |
| `validation/test_native_workflow.py` | `validation/test_calibration_workflow.py` |
| `validation/test_native_y_rotations.py` | `validation/test_y_rotations.py` |
| `calibration/native_operations.py` | `geometry/operations.py`, `calibration/reconstruction/operations.py`, `calibration/alignment/operations.py`, `calibration/alignment/derivatives.py` |
| `validation/telescope_alignment/check.py` | `calibration/alignment/mille.py`, `validation/telescope_alignment/check.py` |
| `validation/telescope_closure/multi_sensor.py` | `calibration/alignment/sensors.py`, `validation/telescope_closure/multi_sensor.py` |
| `validation/telescope_closure/rotation.py` | `calibration/alignment/modes.py`, `geometry/rotations.py`, `validation/telescope_closure/rotation.py` |

Empty obsolete Python package markers `digitization/__init__.py` and `alignment/__init__.py` are removed; no compatibility wrapper is added.

The before-change AST import graph, source hashes and existing result hashes are recorded in `/private/tmp/eic-alignment-layout-20261101/before.json` (metadata only). The maintained dependency map records the final graph.

## Before and after

```text
Before                                  After
simulation/native.py                    simulation/{geant4,digitization}.py
digitization/native.py                  geometry/{prepare,operations,rotations,safety}.py
calibration/native_{controller,          calibration/{workflow,controller,policy,
  operations,policy}.py                   convergence,runner,queue_manager}.py
alignment/pede.py                       calibration/reconstruction/operations.py
validation/... runtime + tests          calibration/alignment/{operations,pede,mille,
localDetEICrecon/... C++                   modes,sensors,information,observability,
                                          derivatives}.py + config/
                                        validation/... tests and independent fixtures
                                        localDetEICrecon/... C++ + geometry/ + tools/
```

`evaluate_native` becomes `evaluate_alignment`; its convergence equations,
thresholds and call signature are unchanged. Reconstruction invocations and
Pede steering are extracted without numerical rewrites. The independent
validation drivers import the relocated implementations while their injection
fixtures and scientific assertions remain under validation.

Remaining `native` names identify immutable schemas/fields, established C++
APIs/targets, historical provenance/receipts, or compatibility-sensitive output
and diagnostic strings. They are not blindly rewritten. New result paths are
independent of the historical names. Empty obsolete package directories are
removed only after moving their contents; no datasets or prior results move.
