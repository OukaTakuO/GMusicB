import os
import re
import sys
import time
import unicodedata
from pathlib import Path

from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo


TICKS_PER_BEAT = 480

# exe化した場合はexeのある場所、通常実行ならこのファイルのある場所を基準にする
# （ダブルクリックやドラッグ&ドロップで起動しても、保存先がずれないように）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

SONGS_DIR = BASE_DIR / "songs"
OUTPUT_DIR = BASE_DIR / "output"

INSTRUMENTS = {
    "Grand Piano": 0,
    "Oboe": 68,
    "Clarinet": 71,
    "Flute": 73
}

DEFAULT_INSTRUMENT = "Grand Piano"
DEFAULT_VOLUME = 100
DEFAULT_PAN = 64
DEFAULT_VELOCITY = 100

REST_WORDS = ("休", "r", "R", "rest")

SETTING_KEYS = ("tempo", "instrument", "volume", "pan")


class ScoreError(Exception):
    """楽譜ファイルのエラー（複数件まとめて保持する）"""

    def __init__(self, messages):
        super().__init__("\n".join(messages))
        self.messages = messages


# ============================================================
# 音名・MIDI変換
# ============================================================

def convert_note_name(note_name):

    NOTE_NAMES_JP = {
        "ド": "C",
        "レ": "D",
        "ミ": "E",
        "ファ": "F",
        "ソ": "G",
        "ラ": "A",
        "シ": "B",
    }

    for jp, en in NOTE_NAMES_JP.items():
        if note_name.startswith(jp):
            return en + note_name[len(jp):]

    return note_name


def note_name_to_midi(note_name):

    NOTE_OFFSETS = {
        "C": 0,
        "C#": 1,
        "Db": 1,
        "D": 2,
        "D#": 3,
        "Eb": 3,
        "E": 4,
        "F": 5,
        "F#": 6,
        "Gb": 6,
        "G": 7,
        "G#": 8,
        "Ab": 8,
        "A": 9,
        "A#": 10,
        "Bb": 10,
        "B": 11,
    }

    if len(note_name) == 2:
        note = note_name[0]
        octave = int(note_name[1])

    elif len(note_name) == 3:
        note = note_name[:2]
        octave = int(note_name[2])

    else:
        raise ValueError(f"無効な音名です: {note_name}")

    if note not in NOTE_OFFSETS:
        raise ValueError(f"対応していない音名です: {note}")

    return (octave + 1) * 12 + NOTE_OFFSETS[note]


def checked_note_name(raw_name):
    """入力された音名を英語表記に直し、MIDI番号に変換できるか確認する"""
    try:
        name = convert_note_name(raw_name)
        midi_note = note_name_to_midi(name)
    except ValueError:
        raise ValueError(f"音名が不正です: {raw_name}")

    if not 0 <= midi_note <= 127:
        raise ValueError(f"音域の範囲外です: {raw_name}")

    return name


# ============================================================
# 楽譜テキストの解析
# ============================================================

def parse_number(text, label):
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{label}が数字ではありません: {text}")


def parse_int(text, label):
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{label}は整数で指定してください: {text}")


def parse_segment(segment, current_time):
    """
    1つの音符・和音・休符を解析する。
    戻り値: (追加する音のリスト, 次の現在位置)
    """
    text = segment.strip()
    start = current_time

    # 開始拍数指定
    if text.startswith("@"):
        space_index = text.find(" ")

        if space_index == -1:
            raise ValueError(
                "開始拍数指定は「@開始拍数 音名 拍数」の形式で入力してください。"
            )

        start = parse_number(text[1:space_index], "開始拍数")
        text = text[space_index + 1:].strip()

        if start < 0:
            raise ValueError("開始拍数は0以上で指定してください。")

    # 和音 / 単音・休符
    is_chord = text.startswith("[")

    if is_chord:
        close_index = text.find("]")

        if close_index == -1:
            raise ValueError("和音は [ド4 ミ4 ソ4] 2 のように指定してください。")

        names = text[1:close_index].split()
        args = text[close_index + 1:].split()

        if not names:
            raise ValueError("和音の音名を指定してください。")

    else:
        parts = text.split()

        if not parts:
            raise ValueError("音名と拍数を指定してください。")

        names = [parts[0]]
        args = parts[1:]

    is_rest = (not is_chord) and names[0] in REST_WORDS

    # 拍数・velocity
    if not args:
        raise ValueError("拍数を指定してください。")

    max_args = 1 if is_rest else 2

    if len(args) > max_args:
        if is_rest:
            raise ValueError("休符は「休 拍数」の形式で入力してください。")
        raise ValueError("「音名 拍数 [ベロシティ]」の形式で入力してください。")

    length = parse_number(args[0], "拍数")

    if length <= 0:
        raise ValueError("拍数は0より大きい値を指定してください。")

    velocity = DEFAULT_VELOCITY

    if len(args) == 2:
        velocity = parse_int(args[1], "ベロシティ")

        if not 0 <= velocity <= 127:
            raise ValueError("ベロシティは0～127で指定してください。")

    notes = []

    if not is_rest:
        for raw_name in names:
            notes.append([checked_note_name(raw_name), start, length, velocity])

    return notes, start + length


def parse_score(text):
    """楽譜テキストを解析して data(dict) を返す。エラーは全件まとめて ScoreError にする"""
    tempo = None
    tracks = []
    current = None
    current_time = 0
    errors = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):

        # 全角の数字・記号・スペースを半角にそろえる
        line = unicodedata.normalize("NFKC", raw_line)

        # コメント除去（C#4 の # は音名なので、行頭または空白の直後の # だけ）
        line = re.sub(r"(^|\s)#.*$", "", line).strip()

        if not line:
            continue

        prefix = f"{current['name']}トラック " if current else ""

        try:
            # [track: 名前]
            match = re.match(r"\[\s*track\s*:\s*(.*?)\s*\]$", line, re.IGNORECASE)

            if match:
                name = match.group(1) or f"Track{len(tracks) + 1}"

                current = {
                    "name": name,
                    "instrument": DEFAULT_INSTRUMENT,
                    "volume": DEFAULT_VOLUME,
                    "pan": DEFAULT_PAN,
                    "expression": [],
                    "sustain": [],
                    "modulation": [],
                    "notes": [],
                }
                tracks.append(current)
                current_time = 0
                continue

            # 設定行（key: value）
            match = re.match(r"([A-Za-z]+)\s*:\s*(.*)$", line)

            if match:
                key = match.group(1).lower()
                value = match.group(2).strip()

                if key not in SETTING_KEYS:
                    raise ValueError(
                        f"不明な設定項目です: {match.group(1)}"
                        f"（使えるもの: {', '.join(SETTING_KEYS)}）"
                    )

                if key == "tempo":
                    if current is not None:
                        raise ValueError(
                            "tempoはトラック定義（[track: ...]）より前に書いてください。"
                        )

                    tempo = parse_int(value, "tempo")

                    if tempo <= 0:
                        raise ValueError("tempoは1以上で指定してください。")

                    continue

                if current is None:
                    raise ValueError(
                        f"{key}は [track: 名前] の下に書いてください。"
                    )

                if key == "instrument":
                    if value not in INSTRUMENTS:
                        raise ValueError(
                            f"対応していない楽器です: {value}"
                            f"（使えるもの: {', '.join(INSTRUMENTS)}）"
                        )

                    current["instrument"] = value

                else:
                    number = parse_int(value, key)

                    if not 0 <= number <= 127:
                        raise ValueError(f"{key}は0～127で指定してください。")

                    current[key] = number

                continue

            # 音符行
            if current is None:
                raise ValueError(
                    "音符は [track: 名前] の下に書いてください。"
                )

            for segment in line.split("|"):
                if not segment.strip():
                    continue

                notes, current_time = parse_segment(segment, current_time)
                current["notes"].extend(notes)

        except ValueError as e:
            errors.append(f"{prefix}{lineno}行目: {e}")

    if tempo is None:
        errors.append("tempoが指定されていません（例: tempo: 120）")

    if not tracks:
        errors.append("トラックがありません（例: [track: Melody]）")

    if errors:
        raise ScoreError(errors)

    return {"tempo": tempo, "tracks": tracks}


# ============================================================
# MIDI生成
# ============================================================

def add_control_change(events, time, control, value):
    events.append(
        {
            "time": time,
            "type": "control_change",
            "control": control,
            "value": value,
        }
    )


def add_note_events(events, start, length, midi_note, velocity):
    events.append(
        {
            "time": round(start * TICKS_PER_BEAT),
            "type": "note_on",
            "note": midi_note,
            "velocity": velocity,
        }
    )

    events.append(
        {
            "time": round((start + length) * TICKS_PER_BEAT),
            "type": "note_off",
            "note": midi_note,
            "velocity": velocity,
        }
    )


def create_track_events(track_data, pan, volume):
    # イベント格納用
    events = []

    instrument_name = track_data["instrument"]
    if instrument_name not in INSTRUMENTS:
        raise ValueError(f"対応していない楽器です: {instrument_name}")

    program = INSTRUMENTS[instrument_name]

    # Program Change
    events.append(
        {
            "time": 0,
            "type": "program_change",
            "program": program,
        }
    )

    # Pan
    add_control_change(events, 0, 10, pan)

    # Volume
    add_control_change(events, 0, 7, volume)

    # Expression
    for time_beat, value in track_data["expression"]:
        if not 0 <= value <= 127:
            raise ValueError(f"Expressionは0～127で指定してください: {value}")

        add_control_change(events, round(time_beat * TICKS_PER_BEAT), 11, value)

    # Sustain
    for time_beat, value in track_data["sustain"]:
        if not 0 <= value <= 127:
            raise ValueError(f"Sustainは0～127で指定してください: {value}")

        add_control_change(events, round(time_beat * TICKS_PER_BEAT), 64, value)

    # Modulation
    for time_beat, value in track_data["modulation"]:
        if not 0 <= value <= 127:
            raise ValueError(f"Modulationは0～127で指定してください: {value}")

        add_control_change(events, round(time_beat * TICKS_PER_BEAT), 1, value)

    # Notes
    for note_name, start, length, velocity in track_data["notes"]:

        if not 0 <= velocity <= 127:
            raise ValueError(f"Velocityは0〜127で指定してください: {velocity}")

        midi_note = note_name_to_midi(note_name)

        add_note_events(events, start, length, midi_note, velocity)

    return events


# 同時刻のイベントの並び順（設定 → note_off → note_on）
# 同じ高さの音が連続しても、前の音を止めてから次の音を鳴らすため
EVENT_PRIORITY = {
    "program_change": 0,
    "control_change": 0,
    "note_off": 1,
    "note_on": 2,
}


def build_midi(data, midi_path):
    mid = MidiFile(ticks_per_beat=TICKS_PER_BEAT)

    # Tempo
    tempo_track = MidiTrack()
    mid.tracks.append(tempo_track)

    tempo_track.append(
        MetaMessage(
            "set_tempo",
            tempo=bpm2tempo(data["tempo"]),
            time=0
        )
    )

    for track_data in data["tracks"]:

        track = MidiTrack()
        mid.tracks.append(track)

        track.append(MetaMessage("track_name", name=track_data["name"], time=0))

        events = create_track_events(
            track_data,
            track_data["pan"],
            track_data["volume"]
        )

        # イベント時系列順に並べ替え
        events.sort(key=lambda event: (event["time"], EVENT_PRIORITY[event["type"]]))

        # 絶対時間 → 相対時間
        previous_time = 0

        for event in events:
            delta_time = event["time"] - previous_time

            if event["type"] == "program_change":

                track.append(
                    Message(
                        "program_change",
                        program=event["program"],
                        time=delta_time
                    )
                )

            elif event["type"] == "control_change":

                track.append(
                    Message(
                        "control_change",
                        control=event["control"],
                        value=event["value"],
                        time=delta_time
                    )
                )

            else:

                track.append(
                    Message(
                        event["type"],
                        note=event["note"],
                        velocity=event["velocity"],
                        time=delta_time
                    )
                )

            previous_time = event["time"]

    midi_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(midi_path)


# ============================================================
# コマンド: build / watch
# ============================================================

def score_path(project_name):
    return SONGS_DIR / f"{project_name}.txt"


def midi_path_of(project_name):
    return OUTPUT_DIR / f"{project_name}.mid"


def resolve_score(arg):
    """
    プロジェクト名、または .txt のパスから (プロジェクト名, 楽譜ファイルのパス) を返す。
    ドラッグ&ドロップで渡されたパスにも対応する。
    """
    arg = arg.strip().strip('"')
    path = Path(arg)

    if path.suffix.lower() == ".txt" or path.is_file():
        return path.stem, path.resolve()

    return arg, score_path(arg)


def build(project_arg):
    """楽譜ファイルからMIDIを生成する。成功したらTrue"""
    project_name, path = resolve_score(project_arg)

    if not path.exists():
        print(f"楽譜ファイルが見つかりません: {path}")
        print("先に new で作成してください。")
        return False

    # utf-8-sig: メモ帳がBOM付きUTF-8で保存しても読めるように
    text = path.read_text(encoding="utf-8-sig")

    try:
        data = parse_score(text)

    except ScoreError as e:
        print(f"\n楽譜にエラーがあります（{len(e.messages)}件）:")
        for message in e.messages:
            print(f"　・{message}")
        return False

    midi_path = midi_path_of(project_name)

    try:
        build_midi(data, midi_path)

    except PermissionError:
        print(f"\nMIDIファイルを書き込めません: {midi_path}")
        print("DAWなど他のソフトで開いたままになっていませんか？")
        return False

    note_count = sum(len(track["notes"]) for track in data["tracks"])

    print(
        f"\nMIDIを保存しました: {midi_path}"
        f"（{len(data['tracks'])}トラック / {note_count}音 / Tempo {data['tempo']}）"
    )
    return True


def watch(project_arg):
    project_name, path = resolve_score(project_arg)

    print(f"{path} を監視しています。保存すると自動でMIDIを作り直します。")
    print("終了: Ctrl+C")

    last_mtime = None

    try:
        while True:
            if path.exists():
                mtime = path.stat().st_mtime

                if mtime != last_mtime:
                    last_mtime = mtime

                    # 保存途中のファイルを読まないよう少し待つ
                    time.sleep(0.2)

                    print(f"\n--- {time.strftime('%H:%M:%S')} 変更を検知 ---")
                    build(str(path))

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n監視を終了します。")


# ============================================================
# コマンド: new
# ============================================================

def input_tempo():
    while True:
        try:
            tempo = int(input("Tempoを入力してください:"))

            if tempo <= 0:
                print("Tempoは1以上で指定してください。")
                continue

            return tempo

        except ValueError:
            print("数字で入力してください。")


def input_instrument():
    names = list(INSTRUMENTS.keys())

    print("\n使用できる楽器:")
    for i, name in enumerate(names, start=1):
        print(f"　{i}. {name}")

    while True:
        text = input("楽器の番号（または名前）を入力してください:").strip()

        if text.isdigit() and 1 <= int(text) <= len(names):
            return names[int(text) - 1]

        if text in INSTRUMENTS:
            return text

        print("一覧にある番号または名前で入力してください。")


def input_yes_no(message):
    while True:
        answer = input(f"{message} (y/n): ").strip().lower()

        if answer in ("y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print("y か n で入力してください。")


SCORE_GUIDE = """\
# ==============================================
#  GMusicB 楽譜ファイル
# ==============================================
# 「#」から行末まではコメントです（自由にメモを書けます）。
#
# 【音の書き方】  音名 拍数 [ベロシティ]
#   ド4 2           ド(C4)を2拍
#   C4 1 80         英語表記もOK。ベロシティ80（省略すると100）
#   [ド4 ミ4 ソ4] 2  和音を2拍
#   休 2            2拍の休み（r 2 でもOK）
#   @8 ソ4 1        8拍目から鳴らす（開始位置を指定）
#   ド4 1 | レ4 1 | ミ4 2   「|」は見やすさ用の区切り（小節ごとに使うと便利）
#
# 音は上から順に、前の音の直後へ自動で並びます。
# 途中に1行足したり消したりしても、後ろの音は自動でずれます。
#
# 【使える楽器】 """ + ", ".join(INSTRUMENTS) + """
# 【Volume / Pan】 0～127（Panは64が中央）
# ==============================================
"""


def new_project(project_name):
    project_name = project_name.strip().strip('"')

    if project_name.lower().endswith(".txt"):
        project_name = project_name[:-4]

    path = score_path(project_name)

    if path.exists():
        print(f"既に存在します: {path}")

        if not input_yes_no("上書きしますか？（今の楽譜は消えます）"):
            print("中止しました。")
            return

    tempo = input_tempo()

    tracks = []

    while True:
        print(f"\n=== Track {len(tracks) + 1} ===")

        default_name = f"Track{len(tracks) + 1}"
        name = input(f"トラック名を入力してください [{default_name}]:").strip()
        name = name or default_name

        tracks.append((name, input_instrument()))

        if not input_yes_no("もう1トラック作りますか？"):
            break

    lines = [SCORE_GUIDE, f"tempo: {tempo}", ""]

    for name, instrument in tracks:
        lines += [
            f"[track: {name}]",
            f"instrument: {instrument}",
            f"volume: {DEFAULT_VOLUME}",
            f"pan: {DEFAULT_PAN}",
            "",
            "# ここから音符を書いてください",
            "",
            "",
        ]

    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n楽譜ファイルを作成しました: {path}")
    print("楽譜を開きます。音符を書いて保存してください。")
    print("保存したら、この画面に戻って「2」を選ぶとMIDIができます。")

    # Windowsでは、.txtに関連付けられたアプリ（通常はメモ帳）で自動的に開く
    if hasattr(os, "startfile"):
        try:
            os.startfile(path)
        except OSError:
            pass


# ============================================================
# エントリーポイント
# ============================================================

COMMANDS = {
    "new": new_project,
    "build": build,
    "watch": watch,
}


def choose_project(existing_only):
    """メニュー操作用: プロジェクトを選ぶ（既存の楽譜は一覧から番号で選べる）"""
    songs = sorted(p.stem for p in SONGS_DIR.glob("*.txt")) if SONGS_DIR.exists() else []

    if existing_only and songs:
        print("\n楽譜一覧:")
        for i, name in enumerate(songs, start=1):
            print(f"　{i}. {name}")

        text = input("番号または名前を入力してください:").strip().strip('"')

        if text.isdigit() and 1 <= int(text) <= len(songs):
            return songs[int(text) - 1]

        return text

    return input("作曲プロジェクト名を入力してください:").strip().strip('"')


def run_command(command, project_arg):
    if not project_arg:
        print("プロジェクト名を指定してください。")
        return

    COMMANDS[command](project_arg)


def menu_loop():
    while True:
        print("\n=== GMusicB ===")
        print("1. new   : 新しい楽譜ファイルを作る")
        print("2. build : 楽譜からMIDIを作る")
        print("3. watch : 保存するたびに自動でMIDIを作る")
        print("q. 終了")

        choice = input("番号を選んでください。").strip().lower()

        if choice in ("q", "quit", "exit"):
            return

        command = {"1": "new", "2": "build", "3": "watch"}.get(choice)

        if command is None:
            print(f"不明な選択です: {choice}")
            continue

        try:
            run_command(command, choose_project(command != "new"))

        except Exception as e:
            # 1回の操作が失敗しても、メニューに戻れるようにする
            print(f"\n予期しないエラーが発生しました: {e}")


def main():
    args = sys.argv[1:]

    if args and args[0] in COMMANDS:
        # 例: GMusicB.exe watch 楽譜.txt / GMusicB.exe new
        command = args[0]
        project_arg = args[1] if len(args) >= 2 else choose_project(command != "new")
        run_command(command, project_arg)

    elif args:
        # .txt をドラッグ&ドロップされた場合: そのままMIDI作成
        run_command("build", args[0])

    else:
        menu_loop()


def run():
    try:
        main()

    except Exception as e:
        print(f"\n予期しないエラーが発生しました: {e}")

    # exeをダブルクリック/ドロップで起動した場合、結果を読む前にウィンドウが閉じないようにする
    if getattr(sys, "frozen", False) and len(sys.argv) > 1:
        input("\nEnterキーを押すと閉じます...")


if __name__ == "__main__":
    run()
