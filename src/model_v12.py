import joblib
import pandas as pd
from pathlib import Path


class V12Model:
    def __init__(self, base_directory):
        self.base_directory = Path(base_directory)
        self.model_bundle = None

    def load(self):
        self.model_bundle = load_v12(self.base_directory)
        return self.model_bundle

    def passes_filters(self, score_difference, calculated_target, active_line, q3_line_ratio, final_average_vs_line_pace):
        return passes_filters(score_difference, calculated_target, active_line, q3_line_ratio, final_average_vs_line_pace)

    def predict_win_probability(self, features):
        if self.model_bundle is None:
            raise RuntimeError("Modelo V12 não carregado.")
        return predict_win_probability(self.model_bundle, features)


def load_v12(base_directory):
    caminho = Path(base_directory) / "v12_sniper_final.pkl"
    try:
        model_bundle = joblib.load(caminho)
        print("✅ [V12 SNIPER] Cérebro carregado com sucesso!")
        return model_bundle
    except Exception as erro:
        print(f"⚠️ Modelo V12 indisponível: {erro}")
        return None


def passes_filters(score_difference, calculated_target, active_line, q3_line_ratio, final_average_vs_line_pace):
    return not (
        score_difference < 8
        or calculated_target < active_line + 1
        or q3_line_ratio < 0.72
        or q3_line_ratio > 0.83
        or final_average_vs_line_pace < 1.5
    )


def predict_win_probability(model_bundle, features):
    modelo = model_bundle["modelo"] if isinstance(model_bundle, dict) else model_bundle
    return modelo.predict_proba(pd.DataFrame([features]))[0][1]
