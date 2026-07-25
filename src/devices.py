"""
devices.py — показывает доступные аудиоустройства (микрофоны и динамики).

Запуск:
    python devices.py
"""

import sounddevice as sd


def main() -> None:
    print("Доступные аудиоустройства:\n")

    devices = sd.query_devices()

    for index, device in enumerate(devices):
        inputs = device["max_input_channels"]
        outputs = device["max_output_channels"]

        device_type = []
        if inputs > 0:
            device_type.append("микрофон")
        if outputs > 0:
            device_type.append("динамик")

        print(f"[{index}] {device['name']} ({', '.join(device_type)})")

    default_input, default_output = sd.default.device

    print("\nУстройства по умолчанию:")
    print(f"Вход (микрофон):  {default_input}")
    print(f"Выход (динамик):  {default_output}")


if __name__ == "__main__":
    main()
