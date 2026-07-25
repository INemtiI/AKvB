import numpy as np
import sounddevice as sd

sample_rate = 48000   # Частота дискретизации
frequency = 1000      # Частота звука, Гц
duration = 2          # Продолжительность, секунды
volume = 0.3          # Громкость от 0 до 1

t = np.arange(int(sample_rate * duration)) / sample_rate
sound = volume * np.sin(2 * np.pi * frequency * t)

print("Воспроизвожу звук...")

sd.play(sound, sample_rate)
sd.wait()

print("Готово.")