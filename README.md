<p align="center">
  <img src="static/icon-512.png" width="120" alt="Kettenapp Logo">
</p>

<h1 align="center">Kettenapp</h1>

<p align="center">
  Verschleißteile am Fahrrad verwalten: Ketten, Kassetten und Kettenblätter mit Kilometerstand, Wechsel-Erinnerung, Lager und Strava-Anbindung.<br>
  Selbst gehostet, für eine Person, als Docker-Container.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Version-0.1-f96a0b" alt="Version 0.1">
  <img src="https://img.shields.io/badge/Python-3.12-blue" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Docker-ghcr.io-2496ed" alt="Docker">
</p>

---

## Inhalt

- [Was die App macht](#was-die-app-macht)
- [Funktionen](#funktionen)
- [Installation](#installation)
- [Einstellungen (.env)](#einstellungen-env)
- [Strava verbinden](#strava-verbinden)
- [Benachrichtigungen aufs Handy](#benachrichtigungen-aufs-handy)
- [Zugriff von unterwegs](#zugriff-von-unterwegs)
- [Als App auf dem Handy](#als-app-auf-dem-handy)
- [Update](#update)
- [Datensicherung](#datensicherung)
- [Technik](#technik)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Versionen](#versionen)

## Was die App macht

Wer mehrere Räder fährt und Ketten im Wechsel nutzt, verliert schnell den Überblick, welche Kette wie viele Kilometer gelaufen ist. Die Kettenapp zählt das automatisch mit:

Die App merkt sich, **von wann bis wann welches Teil an welchem Rad montiert war**. Die Kilometer eines Teils sind die Summe aller Fahrten dieses Rads in diesen Zeiträumen. Die Fahrten kommen aus Strava oder werden von Hand eingetragen. Ein Kettenwechsel ist damit ein Tipp, und der Kilometerstand jeder Kette stimmt von selbst.

## Funktionen

### Räder
- Räder aus Strava übernehmen oder von Hand anlegen
- Antrieb pro Rad: 1x/2x/3x, Gänge hinten, Schaltgruppe, Notiz
- Foto pro Rad
- Kurzfassung des Antriebs aus den montierten Teilen, z. B. „2x12 · 50/34 · 11-34“
- Ruhestand für alte Räder (bleiben mit Historie erhalten), Löschen für manuell angelegte Räder

### Ketten, Kassetten, Kettenblätter
- Jedes Teil zählt seine Kilometer über die gesamte Lebensdauer und seit der aktuellen Montage
- Mehrere Teile pro Rad im Wechsel, mit „heute montieren“ als Ein-Tipp-Wechsel
- Montagehistorie pro Teil, Montagedatum auch rückwirkend
- **Wechsel-Intervall für Ketten** (Standard 1000 km seit der Montage) mit Fortschrittsbalken und Ampel: ok, bald, wechseln
- **Verschleißmessung** in Prozent Längung mit Verlauf und eigener Grenze pro Kette
- Foto, Modell, Kaufdatum, Gangzahl, Notiz und Shop-Link pro Teil
- Ruhestand und Löschen
- **Vorlagen:** ein vorhandenes Teil mit einem Tipp als neues Teil anlegen, inklusive Namensvorschlag („Kette A“ → „Kette B“)

### Kilometer
- **Strava-Import** aller Fahrten mit Zuordnung zum Rad, stündlicher Abgleich im Hintergrund
- **Kilometer von Hand** eintragen, z. B. für kurze Pendelfahrten, die nicht aufgezeichnet werden
- Manuell angelegte Räder nachträglich mit dem Strava-Rad zusammenführen

### Lager
- Übersicht aller Teile, die gerade nicht montiert sind: neu oder gebraucht, mit Kilometerstand und Kaufdatum
- Filter „Passend für Rad X“ nach Gangzahl
- Filter nach Tags
- Teile direkt aus dem Lager an ein Rad montieren

### Ersatzteile
- Liste aller aktiven Teile, fällige zuerst
- Link zum Artikel im Shop pro Teil, sonst Suche bei bike24

### Tags
- Frei vergebbare Tags für Räder und Teile, z. B. `SRAM`, `Red`, `AXS`
- Vorschläge aus bereits benutzten Tags
- Eigene Seite pro Tag mit allen Rädern und Teilen

### Benachrichtigungen
- Push-Nachricht aufs Handy über [ntfy](https://ntfy.sh)
- Pro montierter Kette einmal 100 km vor dem Wechsel und einmal beim Erreichen des Intervalls

### Sonstiges
- Anmeldung mit Passwort
- Für das Handy gebaut, mit hellem und dunklem Design
- Als App auf dem Startbildschirm installierbar

## Installation

Die App läuft als zwei Container: die App selbst und eine PostgreSQL-Datenbank. Das fertige Image baut GitHub bei jeder Änderung automatisch und legt es unter `ghcr.io/drakner-chio/kettenapp` ab.

### Voraussetzungen
- Ein Server mit Docker und Docker Compose
- Auf Unraid: das Plugin **Docker Compose Manager**

### Schritte

1. Ordner für die Daten anlegen, z. B. `/mnt/user/appdata/kettenapp/`.

2. Die Datei `docker-compose.yml` aus diesem Repository übernehmen. Auf Unraid: im Compose Manager einen neuen Stack anlegen und den Inhalt einfügen.

3. Die Datei `.env.example` nach `.env` kopieren und ausfüllen, siehe [Einstellungen](#einstellungen-env).

4. Starten:
   ```bash
   docker compose up -d
   ```
   Auf Unraid: „Compose Up“.

5. Im Browser `http://SERVER-IP:8010` öffnen und mit dem Passwort aus der `.env` anmelden.

Die Pfade in der `docker-compose.yml` sind auf Unraid ausgelegt (`/mnt/user/appdata/kettenapp/...`). Auf anderen Systemen die beiden `volumes`-Einträge anpassen.

## Einstellungen (.env)

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `APP_PASSWORD` | ja | Passwort für die Anmeldung in der App |
| `SECRET_KEY` | ja | Lange Zufallszeichenkette für die Sitzungen, z. B. `openssl rand -hex 32` |
| `DB_PASSWORD` | ja | Passwort der Datenbank, nur Buchstaben und Ziffern |
| `BASE_URL` | für Strava | Adresse, unter der die App im Browser geöffnet wird, ohne Schrägstrich am Ende |
| `STRAVA_CLIENT_ID` | für Strava | Client-ID der eigenen Strava-API-Anwendung |
| `STRAVA_CLIENT_SECRET` | für Strava | Client-Secret der eigenen Strava-API-Anwendung |
| `APP_PORT` | nein | Port auf dem Server, Standard `8010` |

Nach einer Änderung an der `.env` muss der Stack neu erstellt werden (`docker compose up -d`). Ein bloßer Neustart übernimmt die Werte nicht.

Ohne die Strava-Variablen läuft die App vollständig mit von Hand eingetragenen Kilometern.

## Strava verbinden

1. Bei Strava unter Einstellungen → „Meine API-Anwendung“ eine Anwendung anlegen.
   - **Website:** die Adresse der App, z. B. `http://meinserver.example.ts.net:8010`
   - **Authorization Callback Domain:** nur der Name ohne `http://` und ohne Port, z. B. `meinserver.example.ts.net`
2. Client-ID und Client-Secret in die `.env` eintragen, ebenso `BASE_URL`.
3. Stack neu erstellen.
4. Die App über genau die Adresse aus `BASE_URL` öffnen und unter „Einstellungen“ auf „Mit Strava verbinden“ tippen.

`BASE_URL` muss exakt dem entsprechen, was in der Adresszeile steht, inklusive `http` oder `https` und Port. Strava leitet nach der Freigabe dorthin zurück.

Beim ersten Verbinden werden alle Räder und Fahrten importiert. Wer vorher schon Räder von Hand angelegt hat, führt sie danach auf der Rad-Seite unter „Mit Strava zusammenführen“ mit dem Strava-Rad zusammen.

## Benachrichtigungen aufs Handy

1. Die App **ntfy** aus dem App Store oder Play Store installieren.
2. Darin einen Kanal mit einem schwer zu erratenden Namen abonnieren.
3. In der Kettenapp unter „Einstellungen“ die Adresse eintragen: `https://ntfy.sh/kanalname`
4. „Speichern und testen“. Es kommt sofort eine Testnachricht.

Wer den Kanalnamen kennt, kann die Nachrichten mitlesen. Darin stehen nur Kettenname, Rad und Kilometer.

## Zugriff von unterwegs

Die App ist für den Betrieb im eigenen Netz gedacht und sollte nicht ohne Weiteres offen ins Internet gestellt werden. Empfohlen ist ein VPN wie [Tailscale](https://tailscale.com):

1. Tailscale auf dem Server und auf dem Handy installieren und mit demselben Konto anmelden.
2. Die App unter `http://SERVERNAME.TAILNET.ts.net:8010` öffnen.

Die Verbindung ist dabei durch Tailscale verschlüsselt, auch ohne HTTPS.

## Als App auf dem Handy

Im Browser die App öffnen und über das Menü „Zum Startbildschirm hinzufügen“ wählen. Sie bekommt dann ein eigenes Symbol.

Unter Android startet die App nur dann im Vollbild ohne Browserleiste, wenn sie über `https://` läuft. Auf dem iPhone funktioniert das auch ohne.

## Update

1. Geänderte Dateien in dieses Repository hochladen.
2. Unter „Actions“ warten, bis der Lauf „Image bauen“ einen grünen Haken zeigt.
3. Auf dem Server das neue Image holen:
   ```bash
   docker compose pull && docker compose up -d
   ```
   Auf Unraid: „Update Stack“.

Neue Datenbankfelder legt die App beim Start selbst an. Vorhandene Daten bleiben erhalten.

## Datensicherung

Alle Daten liegen außerhalb des Containers in zwei Ordnern:

| Ordner | Inhalt |
|---|---|
| `/mnt/user/appdata/kettenapp/db` | Datenbank mit Rädern, Teilen, Fahrten, Montagen und Messungen |
| `/mnt/user/appdata/kettenapp/uploads` | Fotos |

Für eine saubere Sicherung der Datenbank im laufenden Betrieb:

```bash
docker exec kettenapp-db-1 pg_dump -U ketten ketten > kettenapp-sicherung.sql
```

## Technik

| Bereich | Eingesetzt |
|---|---|
| Sprache | Python 3.12 |
| Web-Framework | Flask mit Flask-SQLAlchemy |
| Datenbank | PostgreSQL 16 |
| Webserver | Gunicorn, ein Prozess mit vier Threads |
| Bilder | Pillow, Fotos werden auf 1600 px verkleinert |
| Oberfläche | Server-seitige HTML-Vorlagen (Jinja), kein JavaScript-Framework |
| Auslieferung | Docker-Image über GitHub Actions nach `ghcr.io` |

### Aufbau

```
app.py                 Die gesamte Anwendung: Datenmodell, Strava, Seiten
templates/             HTML-Vorlagen
static/                App-Symbol und Manifest
Dockerfile             Bau des Images
docker-compose.yml     App und Datenbank
.github/workflows/     Automatischer Bau des Images
```

### Datenmodell in Kürze

- **Bike:** ein Rad, optional mit Strava-Kennung
- **Chain:** ein Teil (Kette, Kassette oder Kettenblatt)
- **Mount:** Zeitraum, in dem ein Teil an einem Rad montiert war
- **Ride:** eine Fahrt aus Strava oder ein manueller Kilometereintrag
- **WearCheck:** eine Verschleißmessung an einer Kette

Die Kilometer eines Teils ergeben sich aus allen Fahrten des Rads innerhalb seiner Montagezeiträume.

## Bekannte Einschränkungen

- **Ein Nutzer.** Es gibt genau ein Passwort und keine Benutzerkonten.
- **Kilometer zählen tageweise.** Wird ein Teil an einem Tag gewechselt, gehen alle Fahrten dieses Tages auf das neu montierte Teil.
- **Passend-Filter nur nach Gangzahl.** Ob eine Kassette zum Freilauf oder ein Kettenblatt zur Kurbel passt, erkennt die App nicht.
- **Kettenblätter als Satz.** Ein 2-fach-Satz wie „50/34“ ist ein Teil, nicht zwei einzelne Blätter.
- **Kein eingebautes HTTPS.** Dafür ist ein VPN oder ein Reverse Proxy nötig.
- **Strava-Zugang nur für das eigene Konto.** Die App nutzt eine private Strava-API-Anwendung.
- **Oberfläche nur auf Deutsch.**

## Versionen

### 0.1 – 5. Oktober 2026

Erste Version.

**Grundlagen**
- Räder, Ketten und Montagezeiträume als Datenmodell
- Kilometer pro Kette aus den Fahrten des Rads im Montagezeitraum
- Anmeldung mit Passwort
- Betrieb als Docker-Container mit PostgreSQL

**Kilometer**
- Strava-Anbindung mit Import aller Räder und Fahrten
- Stündlicher Abgleich im Hintergrund
- Kilometer von Hand eintragen und wieder löschen
- Zusammenführen von manuell angelegten Rädern mit Strava-Rädern

**Teile**
- Kassetten und Kettenblätter als eigene Teile neben den Ketten
- Wechsel-Intervall für Ketten mit Fortschrittsbalken und Ampel
- Verschleißmessung in Prozent mit Verlauf
- Teile direkt auf der Rad-Seite anlegen und montieren
- Bearbeiten von Name, Modell, Vorkilometern, Intervall und Verschleißgrenze
- Vorlagen-Funktion zum Anlegen gleicher Teile
- Kaufdatum, Gangzahl und Notiz pro Teil

**Räder**
- Antrieb pro Rad mit Schaltung, Gängen, Schaltgruppe und Notiz
- Fotos für Räder und Teile
- Ruhestand und Löschen für Räder und Teile

**Seiten**
- Übersicht mit Rädern und ihren Teilen
- Lager mit Filter nach Rad und Tag
- Ersatzteile mit Shop-Links
- Einstellungen für Strava und Benachrichtigungen
- Tags für Räder und Teile mit eigener Übersichtsseite

**Benachrichtigungen**
- Push-Nachrichten über ntfy, 100 km vor dem Wechsel und beim Erreichen des Intervalls

**Handy**
- Installierbar auf dem Startbildschirm mit eigenem Symbol
- Helles und dunkles Design

**Auslieferung**
- Automatischer Bau des Images über GitHub Actions

---

<p align="center">Ein Projekt von <b>Drakner-Chio</b></p>
