# spurs-data

NBA.com の統計とスパーズ関連記事を毎日自動取得して `data/` に置くリポジトリ。

- `data/spurs/`  スパーズ戦（フル: traditional/advanced/misc/hustle/tracking/playbyplay）
- `data/league/` その他の試合（ライト: traditional/advanced/tracking）
- `data/news/YYYY-MM-DD/` 記事本文（Markdown）

実行は `.github/workflows/fetch.yml`（毎日 10:00 / 15:00 JST）。Actions タブから手動実行もできる。
