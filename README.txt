APPLE WATCH SE 3 PRICE TRACKER
================================

Questo programma monitora pagine prodotto e invia notifiche Telegram in uscita.
Non riceve comandi Telegram e non è un bot interattivo.

Configurazione monitorata:
- Apple Watch SE 3 GPS, cassa 44 mm Mezzanotte, cinturino M/L non bianco.
- 9 pagine abilitate: Apple (1), Euronics (2), Unieuro, Expert, MediaWorld,
  Trony, Comet e R-Store.
- ePRICE resta disabilitato nel controllo automatico perché la lettura locale
  può essere bloccata; il link resta nel recap per il controllo manuale.
- Amazon non viene letto dal programma: usare il link/ASIN nel recap con Keepa.

INSTALLAZIONE
-------------

1. Apri PowerShell nella cartella del progetto:
   cd "C:\Users\Marco\Desktop\BOT Pierpaolo"

2. Crea e attiva l'ambiente:
   python -m venv .venv
   .venv\Scripts\Activate.ps1

3. Installa le dipendenze:
   python -m pip install -r requirements.txt
   python -m playwright install chromium

4. Copia .env.example in .env e inserisci TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.
   Non copiare token reali in file di esempio, log o repository.

5. Avvia:
   python price_tracker.py

Per controlli continui il computer deve restare acceso, connesso a Internet e
senza sospensione. Se vuoi l'avvio automatico dopo il riavvio, configura
l'Utilità di pianificazione di Windows per eseguire il comando nella cartella
del progetto.

COMPORTAMENTO
-------------

- Primo controllo all'avvio, poi ogni 30 minuti.
- Dopo un calo importante può controllare ogni 10 minuti per un'ora.
- Il recap giornaliero è previsto alle 07:00 nell'ora locale del computer.
- Le notifiche non consegnate restano in coda in prezzi.json e vengono ritentate
  con intervalli crescenti. In caso di timeout Telegram può aver ricevuto il
  messaggio prima che il client rilevi l'errore: un duplicato è possibile.
- Una sola istanza del programma può essere attiva alla volta.
- Lo stato è scritto atomicamente; una copia precedente viene conservata come
  prezzi.json.bak. Se entrambi i file sono illeggibili, il programma si ferma
  invece di azzerare lo storico.

DATI E LIMITI
-------------

prezzi.json contiene prezzo, disponibilità, errori e notifiche in attesa.
Ogni pagina mantiene al massimo 15.000 rilevazioni, quindi lo storico è limitato.
Una pagina può restituire "disponibilità non verificata": in quel caso il
programma non deduce lo stato da prodotti alternativi presenti nella pagina.

Il prezzo viene accettato solo nell'intervallo configurato (150-500 euro) e
la verifica del prodotto usa il titolo, i dati strutturati e la URL curata.
I siti possono cambiare HTML, applicare CAPTCHA/blocchi anti-bot o non rispondere.
In questi casi il controllo può fallire o diventare più conservativo; controlla
sempre la pagina del negozio prima di acquistare. Non è possibile garantire il
100% di disponibilità o correttezza per siti esterni che possono cambiare.

SICUREZZA
---------

- .env contiene il token e il chat ID: non condividerlo.
- .env.example e .env - Copia.example contengono solo segnaposto.
- Il token non viene incluso nei messaggi di errore del programma.
- Se un token reale è finito in una copia o in un repository, revocalo in
  BotFather e sostituiscilo nel file .env.
