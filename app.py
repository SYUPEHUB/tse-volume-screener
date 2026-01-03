import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(page_title="出来高初動スクリーナー（東証）", layout="wide")

st.title("出来高初動スクリーナー（東証）")
st.caption("短期向け：出来高爆増の初動候補（出来高倍率 + 連続性 + 価格未走り）を抽出します。")

# ----------------------------
# Sidebar (settings)
# ----------------------------
with st.sidebar:
    st.header("設定")

    codes_text = st.text_area(
        "東証コード（4桁）を改行 or カンマ区切りで入力（例：7203,6758…）",
        value="7203\n6758\n9984\n8035\n4063\n9432",
        height=180
    )

    st.subheader("データ取得")
    lookback_days = st.slider("取得期間（日）", 60, 260, 160, 10)

    st.subheader("出来高（じわ増え確認）")
    recent_days = st.slider("直近平均（営業日）", 3, 10, 5, 1)
    base_days = st.slider("比較平均（営業日）", 10, 60, 20, 5)
    min_recent_ratio = st.slider("直近/比較（出来高）倍率 下限", 1.0, 5.0, 1.5, 0.1)

    st.subheader("出来高（当日爆増）")
    spike_days = st.slider("当日倍率の比較日数（営業日）", 10, 60, 20, 5)
    min_spike = st.slider("当日出来高倍率（当日/平均）下限", 1.0, 10.0, 3.0, 0.5)

    st.subheader("価格（未走り条件）")
    max_day_change = st.slider("当日騰落率 上限（%）", 1, 15, 5, 1)

    st.subheader("最低流動性")
    min_base_avg_vol = st.number_input("比較期間（base）平均出来高の下限", min_value=0, value=100000, step=50000)

    st.subheader("表示")
    top_n = st.slider("表示件数", 10, 200, 50, 10)


# ----------------------------
# Helpers
# ----------------------------
def parse_codes(text: str) -> list[str]:
    raw = text.replace(",", "\n").splitlines()
    codes = []
    for r in raw:
        r = r.strip()
        if not r:
            continue
        # 4桁コード → 東証(.T)に正規化
        if r.isdigit() and len(r) == 4:
            codes.append(r + ".T")
        else:
            # 既に 7203.T などで入力されてもOK
            codes.append(r)
    return sorted(set(codes))


@st.cache_data(show_spinner=False)
def fetch_ohlcv(ticker: str, period_days: int) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=period_days)

    df = yf.download(
        ticker,
        start=start.date(),
        end=end.date(),
        interval="1d",
        progress=False,
        auto_adjust=False,
        actions=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    if "Date" not in df.columns:
        df.rename(columns={df.columns[0]: "Date"}, inplace=True)

    # 必要列だけ
    keep_cols = ["Date", "Open", "Close", "Volume"]
    for c in keep_cols:
        if c not in df.columns:
            return pd.DataFrame()

    df = df[keep_cols].dropna()
    return df


def safe_pct_change(today_close: float, prev_close: float) -> float:
    if prev_close <= 0:
        return float("nan")
    return (today_close - prev_close) / prev_close * 100.0


# ----------------------------
# Main
# ----------------------------
codes = parse_codes(codes_text)

run = st.button("スクリーニング実行", type="primary")

if run:
    if len(codes) == 0:
        st.error("銘柄コードを入力してください。")
        st.stop()

    st.info(f"対象銘柄数: {len(codes)}（データ取得→計算中）")

    rows = []
    progress = st.progress(0)

    # 必要な最低データ本数を見積もる（余裕を持たせる）
    min_bars = max(recent_days + base_days + 5, spike_days + 5, base_days + 10)

    for i, t in enumerate(codes, start=1):
        df = fetch_ohlcv(t, lookback_days)
        progress.progress(i / len(codes))

        if df.empty or len(df) < min_bars:
            continue

        # 出来高
        vol = df["Volume"].astype(float)

        # 終値（当日/前日）と当日騰落率
        today_close = float(df["Close"].iloc[-1])
        prev_close = float(df["Close"].iloc[-2])
        day_change_pct = safe_pct_change(today_close, prev_close)
        if pd.isna(day_change_pct):
            continue

        # 価格未走りフィルタ：当日騰落率が大きすぎるものを除外
        if day_change_pct > float(max_day_change):
            continue

        # じわ増え（直近平均 vs 比較平均）
        recent_avg = vol.tail(recent_days).mean()
        base_window = vol.tail(recent_days + base_days).head(base_days)
        base_avg = base_window.mean()

        if pd.isna(recent_avg) or pd.isna(base_avg) or base_avg <= 0:
            continue

        if base_avg < float(min_base_avg_vol):
            continue

        recent_ratio = recent_avg / base_avg
        if recent_ratio < float(min_recent_ratio):
            continue

        # 当日出来高倍率（当日/過去spike_days平均※当日は除外）
        today_vol = float(vol.iloc[-1])
        spike_base_window = vol.tail(spike_days + 1).head(spike_days)
        spike_base = spike_base_window.mean()

        if pd.isna(spike_base) or spike_base <= 0:
            continue

        today_ratio = today_vol / spike_base
        if today_ratio < float(min_spike):
            continue

        # 連続性：前日出来高も平均超え（spike_base を使う）
        prev_vol = float(vol.iloc[-2])
        if prev_vol < spike_base:
            continue

        rows.append({
            "Ticker": t,
            "Code": t.replace(".T", ""),
            "最終日": df["Date"].iloc[-1].date(),
            "終値": today_close,
            "当日騰落率(%)": day_change_pct,
            "当日出来高": int(today_vol),
            "当日出来高倍率": today_ratio,
            "直近平均出来高": int(recent_avg),
            "比較平均出来高": int(base_avg),
            "直近/比較倍率": recent_ratio,
        })

    if not rows:
        st.warning("条件に合う銘柄が見つかりませんでした（またはデータ取得できませんでした）。")
        st.stop()

    out = pd.DataFrame(rows)

    # ランキング：当日出来高倍率 → 直近/比較倍率 の順に強いものを上へ
    out = out.sort_values(["当日出来高倍率", "直近/比較倍率"], ascending=False).head(top_n)

    st.subheader("抽出結果（短期・初動候補）")
    st.dataframe(
        out,
        use_container_width=True,
        column_config={
            "終値": st.column_config.NumberColumn(format="%.2f"),
            "当日騰落率(%)": st.column_config.NumberColumn(format="%.2f"),
            "当日出来高倍率": st.column_config.NumberColumn(format="%.2f"),
            "直近/比較倍率": st.column_config.NumberColumn(format="%.2f"),
        }
    )

    st.download_button(
        "CSVでダウンロード",
        data=out.to_csv(index=False).encode("utf-8-sig"),
        file_name="tse_volume_initial_move.csv",
        mime="text/csv"
    )

st.markdown("---")
st.caption("注意：yfinanceは非公式データ取得のため、取得制限・欠損が起きることがあります。引け後の実行が安定します。")