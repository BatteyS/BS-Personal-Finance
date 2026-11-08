# Personal Finance

A monthly check of your money from three bank exports: Fidelity, Bank of America and Amex. Once a month you drop the CSV files into a page on your own computer, and it answers:

- **Did money reach your brokerage account?**
- **Are your 401(k), Roth IRA and HSA on pace to max out this year?**
- **Did you spend less than you earned?**

Green when all three hold, red with the reason when one doesn't. Below the answer: where the month went (a ring of spending by category, with each category's biggest line items), brokerage so far this year, a twelve-month trend, and the transactions still waiting for a category. A budget tab rebuilds your monthly budget from your salary, with federal, Illinois and payroll taxes worked out, and a Budget vs actual tab sets it against what you spent.

Everything stays on your machine: your transactions, budget and rules live in a `data/` folder that git never tracks.

## Get started

### On a Mac

Open Terminal (in Applications, then Utilities), paste this line, and press Return:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.sh)"
```

It installs what's missing, all of it into your own home folder: Apple's Command Line Tools (for git), [uv](https://docs.astral.sh/uv/) (which runs Python), your copy in `~/personal-finance` with its packages, and **Personal Finance** on your Desktop. It asks for no password itself (Apple's window for the Command Line Tools may), and when it finishes, the page is open in your browser. It needs macOS 13 (Ventura) or newer, and running it again is safe: what's done is skipped.

From then on, double-click **Personal Finance** on your Desktop whenever you want the page. It opens in your browser with no Terminal window, and stops by itself twenty minutes after you close its last tab. It also answers the link `bs-personal-finance://launch`, which the **Launch** button on [seanbattey.com](https://seanbattey.com/apps/) uses. One set up before v1.25 learns that link the next time you open it: macOS asks whether Personal Finance may use your Desktop folder, where it lives, and Allow lets it. From a terminal, `sh launch.sh` does the same, and `sh launch.sh --stop` stops it.

### On Windows

Open PowerShell (in the Start menu, type PowerShell, then press Enter), paste this line, and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.ps1 | iex"
```

It installs what's missing, all of it for you alone and with no administrator rights: git (Git for Windows from winget, or MinGit where winget can't), [uv](https://docs.astral.sh/uv/), your copy in `%USERPROFILE%\personal-finance` with its packages, and **Personal Finance** in the Start menu and on your Desktop. When it finishes, the page is open in your browser. It needs Windows 10 or 11, and running it again is safe: what's done is skipped. What it ran is in `%LOCALAPPDATA%\Personal Finance\Personal Finance install.log`.

From then on, click **Personal Finance** in the Start menu or on your Desktop whenever you want the page. It opens in your browser with no window of its own, and stops by itself twenty minutes after you close its last tab. It also answers the link `bs-personal-finance://launch`, which the **Launch** button on [seanbattey.com](https://seanbattey.com/apps/) uses. From a terminal in your copy, `.venv\Scripts\python.exe launch.pyw` does the same, and `.venv\Scripts\python.exe launch.pyw --stop` stops it. Phone access (below) is set up from a Mac for now.

### On Linux, or by hand

With git and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/BatteyS/BS-Personal-Finance.git ~/personal-finance
cd ~/personal-finance
uv run streamlit run dashboard.py --server.address localhost
```

The page opens at http://localhost:8501.

Whichever way you install it, until you set it up, the page runs on example accounts and an example budget, and says so.

### Set it up

The quickest start is to import an export from your bank: the first time, the page asks which account it's for and learns the file's layout (see **Other banks** below), and sets up your accounts as you go. For the rest, or to describe everything at once:

1. **Your accounts.** Copy `accounts.example.toml` to `data/accounts.toml` and make it describe your own accounts: which Fidelity accounts you have (as the Account column of Fidelity's export names them), which count toward the 401(k), Roth IRA and HSA limits, how often you're paid, and which accounts you move money between. The file explains each field.
2. **Your budget.** Run `uv run budget.py --interview`. It asks for your salary, contributions, tax settings and each monthly budget line, and writes `data/budget.toml`. Run it again any time; your answers are the defaults.
3. **Your rules.** Rules sort transactions into categories (`COSTCO` → groceries). They live in `data/rules.csv`, created from `rules.example.csv` the first time, and you add more from the page as new merchants show up.

## On your phone

On a Mac, Personal Finance can be an icon on your phone's home screen too, opening the same page from anywhere, while the page and everything in it stay on your Mac. It takes the free [Tailscale](https://tailscale.com/download) app on the Mac and on the phone, signed in to the same account. Say yes when the installer asks about your phone, or run this in Terminal:

```bash
cd ~/personal-finance && sh install.sh --phone
```

It may print a `login.tailscale.com` link to turn on HTTPS for your tailnet: open it and click **Enable**. Then it prints your Mac's address, `https://<your-mac>.<your-tailnet>.ts.net`. On the phone, sign in to the Tailscale app, open that address in Safari (Chrome on Android), and choose **Share**, then **Add to Home Screen**. The icon opens the page full screen, like an app.

Three things to know:

- **The Mac has to be awake.** Keep it plugged in, and turn on **Prevent automatic sleeping on power adapter when the display is off** in System Settings, Battery, Options. After a restart, someone has to log in on the Mac first.
- **The page stays running** while phone access is on, so the phone always finds it: a LaunchAgent starts it at login and again if it stops. Without phone access, it stops twenty minutes after its last tab closes.
- **Only you can open it.** Tailscale's address only reaches devices on your own tailnet, and the page answers a request from the phone only for the Tailscale account phone access was set up for (`data/phone.json`, which never leaves the Mac).

`uv run phone.py status` says whether it works and what to do if not. To turn it off, which stops the page from running all the time:

```bash
cd ~/personal-finance && uv run phone.py disable
```

## On a server

The page can run as one container on a home server too, reached only over Tailscale, so the server can be headless and the Mac can sleep. `docker-compose.yml` runs the page beside a Tailscale sidecar that joins your tailnet as the node `finance`: the page is then `https://finance.<your-tailnet>.ts.net`, it has no port on the host at all, and it answers only the Tailscale login you name, the way phone access does on a Mac. With Docker on the server and a clone of this repository:

1. Copy `deploy/env.example` to `.env` beside `docker-compose.yml` and fill it in: a Tailscale auth key from your admin console, your Tailscale login, and where the data folder and Tailscale's state should live. The data folder must belong to user id 1000, which the page runs as.
2. Run `docker compose up -d --build`. `docker compose logs tailscale` prints the node's address once it has joined.
3. Open the address on any of your devices. The data folder starts empty: import your exports from the sidebar, and copy your `accounts.toml`, `budget.toml` and `rules.csv` into it from your laptop.

The sidebar's version and **Update** button don't appear in a container; to update, pull and run the same command. The data folder is never touched by an update, and it holds every export and `finance.db`, so back it up the way you would the laptop's.

## Updating

**Personal Finance** on the Desktop brings your copy up to date each time it starts the page, so you normally run the newest version without doing anything. An update is a fast-forward of your copy to the newest release: it never writes over a change of yours, and skips quietly when you're offline. Versions are numbered after the change they were published for, v1.20 and so on, and each is a release at [github.com/BatteyS/BS-Personal-Finance/releases](https://github.com/BatteyS/BS-Personal-Finance/releases).

For a page already running, the sidebar says which version you have, and when a newer one is out, shows an **Update** button. The page then restarts itself on the new version and comes back in a few seconds (if you started it by hand with `streamlit run`, it says to restart it there). If a page that was running during an update ever reaches for something only the new version has, it says so and offers a **Restart** button. Running the installer again does the same, from Terminal:

```bash
cd ~/personal-finance && sh install.sh
```

or on Windows, from PowerShell:

```powershell
cd ~\personal-finance; powershell -ExecutionPolicy Bypass -File install.ps1
```

An update never writes over a change of yours: a copy whose files you've edited is left as it is, and says so. From a terminal, `uv run updates.py` says whether there's a newer version, and `uv run updates.py update` brings it in.

## Each month

1. **Export four CSV files.** Three carry activity, each from about six weeks back through today (overlaps are fine, since repeated rows are skipped); the fourth carries today's balances:
   - Fidelity: Activity & Orders → History, **All accounts**
   - Fidelity again: Portfolio → Positions → **Download**, for Net worth
   - Bank of America: your checking account
   - Amex: your card's activity
2. **Import them** from the page's sidebar.
3. **Read the verdict,** then clear **Uncategorized**: pick each new merchant, check the suggested pattern, choose a category, save. Every save re-sorts all your months.
4. **Glance at Net worth.** Each Fidelity account should carry the positions file's date, and Bank of America its last posted day. A card or loan you've typed a balance for follows its transactions from there; type a fresh one if it has drifted.

**Finance mode.** The **Personal | Finance** toggle at the top right reads the same data as statements, each beside the period before: a balance sheet as of a month, quarter or year end (assets, liabilities and net worth, each line opening to its accounts); an income statement for the period on a cash basis (take-home pay, employer money and dividends in, spending by category out, net of refunds); and a cash flow that reconciles the two. The cash flow's last line is zero when every transaction is categorized and every transfer matched, and otherwise the size of what's missing: an unmatched transfer, an export you haven't imported, a typed balance the transactions disagree with. The page remembers the mode in its address (`?mode=finance`), so a bookmark or the phone's home-screen icon reopens where you left it. Only complete periods are offered; **Year to date** is how the current year reads.

**Net worth.** The second tab shows what every account held at the end of each month, as a line each beside net worth's gold line; a pill per line shows or hides it, and the axis fits the lines shown. It splits each month's change into what the market did and what you did. Balances come from Bank of America's export (its running balance), from the Fidelity positions download (every Fidelity account's value on one day), and from **Record a balance** on the tab for anything else: a card's statement balance, a loan, an HSA held elsewhere. A card or loan is recorded as what you owe. Cash, cards and loans are brought to each month end by their transactions, so one typed balance carries a card from then on; an investment account shows a real reading taken within a week of the month end, or its last reading with the date it was taken.

To fill in the months before your first balances, import activity history going back as far as you like (Fidelity's History for All accounts, and Bank of America's and Amex's activity), alongside a positions download or after it. Cash and cards are worked back exactly from their balances. An investment account's earlier months are estimated, as its first reading less the money put in since, as if the market stood still. They're labeled "est. from" that reading's date and shaded "Estimated" on the chart, and growth is never worked out from them. Each account goes back only as far as its bank's transactions do.

**Other banks.** Fidelity, Bank of America and Amex are built in. For any other institution, download its CSV, or its OFX/QFX ("Quicken" or "Money") file if it offers one, and import it the same way. The first time, the page shows **Teach this file**: the file's first rows, with first guesses at which columns hold the date, the amount and the description, and whether spending shows as negative or positive. Check them against the preview of how the rows will be stored, pick the account (or describe a new one, and for a card, which account pays it), and click **Save and import**. From then on, files with that layout import by themselves. An OFX file only asks which of your accounts it belongs to. What you teach is saved in `data/formats.toml` (`formats.example.toml` shows its shape).

Moves between your own accounts (a card payment, cash to brokerage) are matched up and never count as spending. If one shows up under Uncategorized, the transfer pairs in `data/accounts.toml` need a line for it.

## What's in the data folder

| File | Holds |
| --- | --- |
| `finance.db` | Every transaction imported, with its category, and every balance recorded |
| `exports/` | Every file you've imported, kept |
| `accounts.toml` | Your accounts, the transfers between them, your pay schedule |
| `budget.toml` | Your budget, written by the interview |
| `rules.csv` | Your category rules |
| `formats.toml` | The layouts of other banks' files, and which account an OFX file belongs to |
| `phone.json` | Your Mac's tailnet address and Tailscale login, once phone access is on |

None of it is tracked by git. Back the folder up some other way, such as Time Machine.

## Tests

```bash
uv run pytest
```

All of them run on made-up rows. `uv run export_public.py --check` checks that no shared file carries personal data; CI runs both on every push.

## License

MIT; see [LICENSE](LICENSE).
