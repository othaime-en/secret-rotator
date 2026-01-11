import secrets
import string
from typing import Dict, Any
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.utils.logger import logger


class PasswordRotator(SecretRotator):
    """Generate random passwords with guaranteed character type inclusion"""

    # Define allowed symbols as a class attribute for consistency
    ALLOWED_SYMBOLS = "!@#$%^&*"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.length = config.get("length", 16)
        self.use_symbols = config.get("use_symbols", True)
        self.use_numbers = config.get("use_numbers", True)
        self.use_uppercase = config.get("use_uppercase", True)
        self.use_lowercase = config.get("use_lowercase", True)
        self.exclude_ambiguous = config.get("exclude_ambiguous", False)

        # Cache allowed symbols as a set for O(1) lookup
        self._symbol_set = set(self.ALLOWED_SYMBOLS)

        # Define ambiguous characters to exclude if requested
        self._ambiguous_chars = set("il1Lo0O")

    def generate_new_secret(self) -> str:
        """
        Generate a new random password with GUARANTEED inclusion of
        at least one character from each enabled character type.

        This ensures the generated password will always pass validation.
        """
        # Build character pools for each enabled type
        char_pools = self._build_character_pools()

        if not char_pools:
            logger.error("No character types selected for password generation")
            return ""

        # Calculate how many characters we need from each required pool
        required_chars_count = len(char_pools)

        if self.length < required_chars_count:
            logger.error(
                f"Password length {self.length} is too short to include "
                f"all {required_chars_count} required character types"
            )
            return ""

        # Step 1: Guarantee at least one character from each enabled type
        password_chars = []
        for pool in char_pools.values():
            password_chars.append(secrets.choice(pool))

        # Step 2: Fill remaining length with random characters from all pools combined
        all_chars = "".join(char_pools.values())
        remaining_length = self.length - len(password_chars)

        for _ in range(remaining_length):
            password_chars.append(secrets.choice(all_chars))

        # Step 3: Shuffle to avoid predictable patterns (first chars from each type)
        # Use secrets.SystemRandom for cryptographically secure shuffling
        rng = secrets.SystemRandom()
        rng.shuffle(password_chars)

        password = "".join(password_chars)

        logger.info(
            f"Generated new password of length {len(password)} with "
            f"{required_chars_count} character types"
        )

        return password

    def _build_character_pools(self) -> Dict[str, str]:
        """
        Build separate character pools for each enabled character type.
        Returns a dict mapping type name to its character pool.
        """
        pools = {}

        if self.use_lowercase:
            lowercase = string.ascii_lowercase
            if self.exclude_ambiguous:
                lowercase = "".join(c for c in lowercase if c not in self._ambiguous_chars)
            if lowercase:  # Only add if non-empty after filtering
                pools["lowercase"] = lowercase

        if self.use_uppercase:
            uppercase = string.ascii_uppercase
            if self.exclude_ambiguous:
                uppercase = "".join(c for c in uppercase if c not in self._ambiguous_chars)
            if uppercase:
                pools["uppercase"] = uppercase

        if self.use_numbers:
            numbers = string.digits
            if self.exclude_ambiguous:
                numbers = "".join(c for c in numbers if c not in self._ambiguous_chars)
            if numbers:
                pools["numbers"] = numbers

        if self.use_symbols:
            symbols = self.ALLOWED_SYMBOLS
            # Symbols typically don't have ambiguous characters, but check anyway
            if self.exclude_ambiguous:
                symbols = "".join(c for c in symbols if c not in self._ambiguous_chars)
            if symbols:
                pools["symbols"] = symbols

        return pools

    def validate_secret(self, secret: str) -> bool:
        """
        Validate password meets all requirements.

        This method now serves as a secondary check since generate_new_secret()
        guarantees compliance, but it's still useful for validating externally
        provided passwords or after deserialization.
        """
        # Check for None or empty input
        if not isinstance(secret, str) or not secret:
            logger.error("Invalid secret: must be a non-empty string")
            return False

        # Check length requirement
        if len(secret) < self.length:
            logger.warning(f"Secret length {len(secret)} is less than required {self.length}")
            return False

        # Check if any character types are enabled
        if not (self.use_lowercase or self.use_uppercase or self.use_numbers or self.use_symbols):
            logger.error("No character types enabled for validation")
            return False

        # Validate character type requirements
        checks = []
        if self.use_lowercase:
            has_lowercase = any(c.islower() for c in secret)
            checks.append((has_lowercase, "lowercase"))

        if self.use_uppercase:
            has_uppercase = any(c.isupper() for c in secret)
            checks.append((has_uppercase, "uppercase"))

        if self.use_numbers:
            has_numbers = any(c.isdigit() for c in secret)
            checks.append((has_numbers, "digits"))

        if self.use_symbols:
            has_symbols = any(c in self._symbol_set for c in secret)
            checks.append((has_symbols, "symbols"))

        # Additional check: ensure no ambiguous characters if excluded
        if self.exclude_ambiguous:
            has_ambiguous = any(c in self._ambiguous_chars for c in secret)
            if has_ambiguous:
                logger.warning("Secret contains ambiguous characters")
                checks.append((False, "no_ambiguous_chars"))

        # Check for invalid characters (not in any allowed pool)
        allowed_chars = set()
        pools = self._build_character_pools()
        for pool in pools.values():
            allowed_chars.update(pool)

        invalid_chars = [c for c in secret if c not in allowed_chars]
        if invalid_chars:
            logger.warning(f"Secret contains invalid characters: {invalid_chars}")
            return False

        # Log specific validation failures
        failed_checks = [check_type for check_passed, check_type in checks if not check_passed]
        if failed_checks:
            logger.warning(f"Secret validation failed: missing {', '.join(failed_checks)}")

        return all(check[0] for check in checks)

    def calculate_entropy(self, secret: str) -> float:
        """
        Calculate password entropy in bits.
        Higher entropy = stronger password.

        This is useful for reporting password strength to users/admins.
        """
        if not secret:
            return 0.0

        # Determine character pool size
        pool_size = 0
        if any(c.islower() for c in secret):
            pool_size += 26
        if any(c.isupper() for c in secret):
            pool_size += 26
        if any(c.isdigit() for c in secret):
            pool_size += 10
        if any(c in self._symbol_set for c in secret):
            pool_size += len(self.ALLOWED_SYMBOLS)

        # Entropy = log2(pool_size^length)
        import math

        if pool_size > 0:
            entropy = len(secret) * math.log2(pool_size)
            return round(entropy, 2)
        return 0.0

    def get_strength_assessment(self, secret: str) -> Dict[str, Any]:
        """
        Provide detailed strength assessment of a password.
        Useful for reporting and auditing.
        """
        entropy = self.calculate_entropy(secret)

        # Strength categories based on entropy
        if entropy < 28:
            strength = "very_weak"
        elif entropy < 36:
            strength = "weak"
        elif entropy < 60:
            strength = "moderate"
        elif entropy < 128:
            strength = "strong"
        else:
            strength = "very_strong"

        return {
            "length": len(secret),
            "entropy_bits": entropy,
            "strength": strength,
            "has_lowercase": any(c.islower() for c in secret),
            "has_uppercase": any(c.isupper() for c in secret),
            "has_numbers": any(c.isdigit() for c in secret),
            "has_symbols": any(c in self._symbol_set for c in secret),
            "meets_requirements": self.validate_secret(secret),
        }
