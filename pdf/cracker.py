#!/usr/bin/env python3
import argparse
import itertools
import string
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pikepdf
from tqdm import tqdm

# --- password generation utilities ------------------------------------------

def gen_alpha_only(chars, min_len, max_len):
    """Yield alphabet-only passwords for lengths in [min_len, max_len]."""
    for L in range(min_len, max_len + 1):
        for tup in itertools.product(chars, repeat=L):
            yield ''.join(tup)

def gen_alpha_plus_digits(chars, alpha_min, alpha_max, digit_min, digit_max, digits='0123456789'):
    """
    Yield passwords that are <alphabet part><numeric suffix>.
    - alphabet part length in [alpha_min, alpha_max]
    - numeric suffix length in [digit_min, digit_max]
    """
    for L in range(alpha_min, alpha_max + 1):
        # produce all alphabetic parts of length L
        for alpha_part in itertools.product(chars, repeat=L):
            alpha_str = ''.join(alpha_part)
            for D in range(digit_min, digit_max + 1):
                for digit_part in itertools.product(digits, repeat=D):
                    yield alpha_str + ''.join(digit_part)

def combined_generator(chars, alpha_min, alpha_max, include_alpha_only, include_alpha_plus_digits,
                       digit_min, digit_max, digits='0123456789'):
    """Yield either alpha-only passwords, then alpha+digits passwords (order configurable)."""
    if include_alpha_only:
        yield from gen_alpha_only(chars, alpha_min, alpha_max)
    if include_alpha_plus_digits:
        yield from gen_alpha_plus_digits(chars, alpha_min, alpha_max, digit_min, digit_max, digits)

# --- counting utilities -----------------------------------------------------

def count_alpha_only(chars_len, min_len, max_len):
    return sum(chars_len ** L for L in range(min_len, max_len + 1))

def count_alpha_plus_digits(chars_len, alpha_min, alpha_max, digits_len, digit_min, digit_max):
    total = 0
    for L in range(alpha_min, alpha_max + 1):
        for D in range(digit_min, digit_max + 1):
            total += (chars_len ** L) * (digits_len ** D)
    return total

# --- password try (worker) --------------------------------------------------

def try_password_worker(args_tuple):
    """
    Worker function executed in a separate process.
    args_tuple: (pdf_file, password)
    Returns the password if opened successfully, else None
    """
    pdf_file, password = args_tuple
    try:
        with pikepdf.open(pdf_file, password=password):
            # open successful -> password correct
            return password
    except (pikepdf._core.PasswordError, pikepdf._core.PdfError, pikepdf._qpdf.PdfError, Exception):
        # On any error treat as wrong password. (Some PDF errors might be different types.)
        return None

# --- main decryption loop with sliding window of futures --------------------

def decrypt_pdf_streaming(pdf_file, password_iter, total_passwords, max_workers=4, max_in_flight_multiplier=4):
    """
    Submit password attempts in a bounded streaming manner.
    We keep at most max_workers * max_in_flight_multiplier futures in-flight.
    """
    in_flight_limit = max(8, max_workers * max_in_flight_multiplier)

    executor = ProcessPoolExecutor(max_workers=max_workers)
    futures = dict()  # future -> password
    pbar = tqdm(total=total_passwords, desc="Decrypting PDF", unit="pwd")
    found = None

    try:
        # prime by submitting up to in_flight_limit tasks
        for _ in range(in_flight_limit):
            try:
                pwd = next(password_iter)
            except StopIteration:
                break
            fut = executor.submit(try_password_worker, (pdf_file, pwd))
            futures[fut] = pwd

        # process as futures complete and keep the pipeline full
        while futures:
            for fut in as_completed(futures):
                pwd = futures.pop(fut)
                try:
                    res = fut.result()
                except Exception:
                    res = None
                pbar.update(1)
                if res:
                    found = res
                    # try to cancel remaining futures
                    for f in list(futures):
                        try:
                            f.cancel()
                        except Exception:
                            pass
                    executor.shutdown(wait=False, cancel_futures=True)
                    pbar.close()
                    return found

                # attempt to submit another task to keep pipeline full
                try:
                    next_pwd = next(password_iter)
                    new_fut = executor.submit(try_password_worker, (pdf_file, next_pwd))
                    futures[new_fut] = next_pwd
                except StopIteration:
                    # no more passwords to submit; continue draining existing futures
                    pass

        pbar.close()
        return None

    except KeyboardInterrupt:
        # user interruption: attempt graceful shutdown
        print("\n[!] Interrupted by user. Shutting down workers.")
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        pbar.close()
        raise
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass

# --- file-based wordlist helper --------------------------------------------

def load_passwords_from_file(path):
    with open(path, 'r', errors='ignore') as f:
        for line in f:
            pwd = line.rstrip('\n\r')
            if pwd:
                yield pwd

# --- CLI -------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Try to decrypt a password-protected PDF (alpha or alpha+number combos).")
    parser.add_argument('pdf_file', help='Path to the password-protected PDF')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-w', '--wordlist', help='Path to password wordlist file (one password per line)')
    group.add_argument('-g', '--generate', action='store_true', help='Generate passwords (combinatoric)')

    # alphabet settings
    parser.add_argument('--alpha-chars', default=string.ascii_lowercase, help='Characters for alphabet part (default: lowercase letters)')
    parser.add_argument('--alpha-min', type=int, default=1, help='Minimum length of alphabet part (default: 1)')
    parser.add_argument('--alpha-max', type=int, default=3, help='Maximum length of alphabet part (default: 3)')

    # digits (numeric suffix) settings (only used when generating)
    parser.add_argument('--digit-min', type=int, default=1, help='Minimum length of numeric suffix (when using alpha+digits) (default: 1)')
    parser.add_argument('--digit-max', type=int, default=2, help='Maximum length of numeric suffix (when using alpha+digits) (default: 2)')
    parser.add_argument('--digits', default=string.digits, help='Characters for digit suffix (default: 0-9)')

    # choose which pattern types to attempt when generating
    parser.add_argument('--include-alpha-only', action='store_true', help='Include pure alphabet-only passwords')
    parser.add_argument('--include-alpha-plus-digits', action='store_true', help='Include alphabet + numeric suffix passwords')

    parser.add_argument('--max-workers', type=int, default=4, help='Number of parallel worker processes (default: 4)')
    parser.add_argument('--inflight-multiplier', type=int, default=4, help='Multiplier to control how many futures are in-flight (default: 4). Total in-flight = max_workers * multiplier.')
    return parser.parse_args()

def main():
    args = parse_args()
    pdf_file = args.pdf_file

    if not Path(pdf_file).is_file():
        print("[!] PDF file not found:", pdf_file)
        sys.exit(1)

    # If using a wordlist file, use that generator and count
    if args.wordlist:
        passwords = load_passwords_from_file(args.wordlist)
        # count lines (must scan once) - for progress bar
        total = sum(1 for _ in load_passwords_from_file(args.wordlist))
        print(f"[i] Loaded wordlist with {total} entries.")
    else:
        # generate mode - ensure at least one of the two patterns is selected
        if not args.include_alpha_only and not args.include_alpha_plus_digits:
            print("[!] When using --generate you must set --include-alpha-only and/or --include-alpha-plus-digits")
            sys.exit(1)

        alpha_chars = args.alpha_chars
        digits_chars = args.digits
        alpha_min = args.alpha_min
        alpha_max = args.alpha_max
        digit_min = args.digit_min
        digit_max = args.digit_max

        # calculate total passwords mathematically (no generator exhaustion)
        total = 0
        if args.include_alpha_only:
            total += count_alpha_only(len(alpha_chars), alpha_min, alpha_max)
        if args.include_alpha_plus_digits:
            total += count_alpha_plus_digits(len(alpha_chars), alpha_min, alpha_max, len(digits_chars), digit_min, digit_max)

        print(f"[i] Total passwords to try (calculated): {total:,}")

        # create generator
        passwords = combined_generator(
            chars=alpha_chars,
            alpha_min=alpha_min,
            alpha_max=alpha_max,
            include_alpha_only=args.include_alpha_only,
            include_alpha_plus_digits=args.include_alpha_plus_digits,
            digit_min=digit_min,
            digit_max=digit_max,
            digits=digits_chars
        )

    # Run the streaming decrypt loop
    try:
        found = decrypt_pdf_streaming(pdf_file, iter(passwords), total, max_workers=args.max_workers,
                                      max_in_flight_multiplier=args.inflight_multiplier)
    except KeyboardInterrupt:
        print("[!] Stopped by user.")
        return

    if found:
        print("[+] Password found:", found)
    else:
        print("[-] Password not found.")

if __name__ == "__main__":
    main()
