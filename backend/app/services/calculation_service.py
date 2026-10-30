from decimal import Decimal, InvalidOperation


def calculate(operation: str, values: list[str]) -> str:
    if not values or len(values) > 20:
        raise ValueError("Provide between 1 and 20 numbers.")

    numbers = []

    for value in values:
        try:
            number = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("Invalid number.") from error

        if not number.is_finite() or abs(number) > Decimal("1e12"):
            raise ValueError("Number is outside the supported range.")

        numbers.append(number)

    if operation == "subtract":
        if len(numbers) != 2:
            raise ValueError("Subtract requires exactly two numbers.")

        result = numbers[0] - numbers[1]

    elif operation == "minimum":
        result = min(numbers)

    elif operation == "sum":
        result = sum(numbers, Decimal("0"))

    else:
        raise ValueError("Unsupported calculation operation.")

    return format(result, "f")