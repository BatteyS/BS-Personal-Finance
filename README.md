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

From then on, double-click **Personal Finance** on your Desktop whenever you want the page. It opens in your browser with no Terminal window, and stops by itself ten minutes after you close its last tab. From a terminal, `sh launch.sh` does the same, and `sh launch.sh --stop` stops it.

### On Linux, or by hand

With git and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/BatteyS/BS-Personal-Finance.git ~/personal-finance
cd ~/personal-finance
uv run streamlit run dashboard.py --server.address localhost
```

The page opens at http://localhost:8501.

Either way, until you set it up, the page runs on example accounts and an example budget, and says so.

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
- **The page stays running** while phone access is on, so the phone always finds it: a LaunchAgent starts it at login and again if it stops. Without phone access, it stops ten minutes after its last tab closes.
- **Only you can open it.** Tailscale's address only reaches devices on your own tailnet, and the page answers a request from the phone only for the Tailscale account phone access was set up for (`data/phone.json`, which never leaves the Mac).

`uv run phone.py status` says whether it works and what to do if not. To turn it off, which stops the page from running all the time:

```bash
cd ~/personal-finance && uv run phone.py disable
```

## Updating

The page's sidebar says which version you have, and when a newer one is out, shows an **Update** button. The page then restarts itself on the new version and comes back in a few seconds (if you started it by hand with `streamlit run`, it says to restart it there). If a page that was running during an update ever reaches for something only the new version has, it says so and offers a **Restart** button. Running the installer again does the same, from Terminal:

```bash
cd ~/personal-finance && sh install.sh
```

An update never writes over a change of yours: a copy whose files you've edited is left as it is, and says so. From a terminal, `uv run updates.py` says whether there's a newer version, and `uv run updates.py update` brings it in.

## Each month

1. **Export three CSV files,** each from about six weeks back through today; overlaps are fine, since repeated rows are skipped:
   - Fidelity: Activity & Orders → History, **All accounts**
   - Bank of America: your checking account
   - Amex: your card's activity
2. **Import them** from the page's sidebar.
3. **Read the verdict,** then clear **Uncategorized**: pick each new merchant, check the suggested pattern, choose a category, save. Every save re-sorts all your months.

**Other banks.** Fidelity, Bank of America and Amex are built in. For any other institution, download its CSV, or its OFX/QFX ("Quicken" or "Money") file if it offers one, and import it the same way. The first time, the page shows **Teach this file**: the file's first rows, with first guesses at which columns hold the date, the amount and the description, and whether spending shows as negative or positive. Check them against the preview of how the rows will be stored, pick the account (or describe a new one, and for a card, which account pays it), and click **Save and import**. From then on, files with that layout import by themselves. An OFX file only asks which of your accounts it belongs to. What you teach is saved in `data/formats.toml` (`formats.example.toml` shows its shape).

Moves between your own accounts (a card payment, cash to brokerage) are matched up and never count as spending. If one shows up under Uncategorized, the transfer pairs in `data/accounts.toml` need a line for it.

## What's in the data folder

| File | Holds |
| --- | --- |
| `finance.db` | Every transaction imported, with its category |
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
