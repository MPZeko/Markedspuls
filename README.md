# Markedspuls

Automatiske, beslutningsorienterede markedsbriefs på dansk – uden generativ AI.

## Leverancer

- Morgenbrief kl. 07.30 i `Europe/Copenhagen`
- USA-premarket kl. 09.00 i `America/New_York` (30 minutter før normal åbning)
- Dagsopsummering kl. 16.15 i `America/New_York`
- Kort besked på amerikanske markedshelligdage
- Discord-post via repository-secret `DISCORD_WEBHOOK_URL`
- Dashboarddata i `data/latest.json` og historik i `data/archive/`

Tidsplanerne anvender IANA-tidszoner og følger derfor sommer-/vintertid.

## Datadækning

Indeks, futures, Asien, Europa, VIX, amerikansk 10-årig rente, valuta, olie,
guld, Bitcoin og Ethereum hentes via Yahoo Finance. Watchlisten omfatter:

`AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `TSLA`, `COIN`, `MSTR`,
`IONQ`, `QUBT`, `MP`, `RGTI` og `IREN`.

SpaceX er ikke børsnoteret og behandles derfor kun som en privat
selskabs-/nyhedsovervågning.

## Transparente vurderingsregler

Risk-regimet beregnes som `risk-on`, `neutral` eller `risk-off` ud fra:

- S&P 500- og Nasdaq-futures
- VIX-niveau
- Bitcoins døgnbevægelse
- usædvanligt kraftige olieprisbevægelser

Scoren og de udløsende signaler offentliggøres sammen med briefet.

## Manuel test

Åbn **Actions → Market briefs → Run workflow**, vælg brief og kør.

## Begrænsninger

Yahoo Finance er en praktisk informationskilde, men ikke et garanteret
professionelt realtidsfeed. Briefet markerer datatidspunkt og viser `n/a`, hvis
en kilde ikke svarer. Nyhedsoverskrifter udvælges automatisk fra ticker-feeds;
de fortolkes ikke af en sprogmodel.

