# Doctor NoX roadmap

Where Doctor NoX is going, and in what order. The order matters more than the
list: each stage only makes sense once the one before it is done.

## The goal

A doctor for any Linux PC that a casual user reaches for when something is
wrong, and that a distribution can ship by default without a second thought.

## The promise that must not break

Everything else can change. These cannot:

- It only examines. No root, no changes to the system, no network requests,
  nothing uploaded.
- It never says "healthy" about something it could not examine.
- Every finding says what it means and what to do, in plain words.
- It works on every Linux, not on one distribution.

A feature that needs any of these bent is not added.

## Stages

### 1. Release it

Nothing counts until people can install it.

- Publish the first release, with signed packages for Fedora, Debian and
  Ubuntu, Arch and openSUSE, the Python package and the Flatpak bundle.
- Check each package on a real installation of its distribution, not only in
  the test containers.

### 2. Prove it on real machines

A diagnostic tool lives or dies on being right. One false "Needs treatment"
and it is uninstalled.

- Run it on 20 to 50 different PCs: laptops and desktops, AMD, Intel and
  NVIDIA, KDE and GNOME, image-based and package-based.
- Make a wrong diagnosis easy to report: an issue form that takes the redacted
  doctor's note.
- Fix every false alarm before adding anything. Review the wording of every
  treatment with someone who is not a Linux person.
- Look at the window on real screens: sizes, scaling, both themes, the title
  bar on each desktop. Cut or hide whatever turns out to be noise.

### 3. Make it easy to find

- Flathub. This is where casual users on every distribution find apps.
- The distributions' own channels: AUR, a Fedora COPR, a PPA.
- A project page with screenshots and a one-minute "what it found on my PC".

### 4. Speak more than English

Distributions expect translations for anything they ship by default.

- Make every string in the window translatable.
- Start with the languages of the people who tested it.

### 5. Get adopted

- FE-XicoOS first.
- Then community distributions whose users match what Doctor NoX tests well:
  gaming-focused and image-based ones.
- The large distributions last, once there is a track record to show them.

## Ideas that wait

Good ideas that are held back on purpose, with what each one is waiting for.

| Idea | Waiting for |
|---|---|
| A quiet weekly checkup that notifies only when something got worse | Testing on real desktops that the notification appears, and a decision on whether "runs only when you open it" may change |
| Vital signs outside the window (a tray icon, or a Plasma widget) | Stage 2: knowing which readings people actually look at |
| Printing | A read-only way to ask the print system that works on every distribution |
| Disk health (SMART) | A way to read it without administrator rights |
| The doctor's words in `--text` and `--summary` | Nobody depending on the current report wording |

## How a new idea is judged

1. Does a casual user with a real problem need it?
2. Does it work on every Linux, or say clearly where it does not apply?
3. Can it be wrong in a way that worries someone for nothing?
4. Does it keep the promise above?
5. Can it be tested without a person watching?

If the answer to 3 is yes or to any other is no, it waits.
