"""LightGBM, imported safely on Windows.

lib_lightgbm.dll 4.7 is built against MSVC runtime >= 14.40. With an older system msvcp140.dll (14.36 on
the dev laptop) its std::mutex dereferences null, so every Dataset.set_field (labels!) dies with
"access violation reading 0x0". scikit-learn's wheel bundles msvcp140.dll/vcomp140.dll 14.51 and
preloads them on import; once they are loaded, lib_lightgbm binds to them instead of the system copy.
So sklearn must be imported before lightgbm, in every process, before anything else imports lightgbm.
The real fix is a current "Visual C++ Redistributable 2015-2022 (x64)" on the machine.
"""

# isort: off
import sklearn  # noqa: F401
import lightgbm as lgb

# isort: on

__all__ = ["lgb"]
