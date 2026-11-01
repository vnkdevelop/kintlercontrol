use crate::{identity::Config, network};
use anyhow::Result;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Frame, Terminal,
    backend::{CrosstermBackend, TestBackend},
    layout::{Alignment, Constraint, Layout},
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
    widgets::{Block, Paragraph, Wrap},
};
use std::{
    collections::VecDeque,
    io::{self, IsTerminal},
    sync::{Arc, mpsc},
    time::Duration,
};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::System::Console::{
    AllocConsole, FreeConsole, GetConsoleWindow, SetConsoleCP, SetConsoleOutputCP, SetConsoleTitleW,
};

const BG: Color = Color::Rgb(9, 14, 11);
const FG: Color = Color::Rgb(216, 229, 219);
const GREEN: Color = Color::Rgb(143, 204, 164);
const MUTED: Color = Color::Rgb(127, 148, 133);

const DIGITS: [[&str; 5]; 10] = [
    ["██████", "██  ██", "██  ██", "██  ██", "██████"],
    ["  ██  ", "████  ", "  ██  ", "  ██  ", "██████"],
    ["██████", "    ██", "██████", "██    ", "██████"],
    ["██████", "    ██", " █████", "    ██", "██████"],
    ["██  ██", "██  ██", "██████", "    ██", "    ██"],
    ["██████", "██    ", "██████", "    ██", "██████"],
    ["██████", "██    ", "██████", "██  ██", "██████"],
    ["██████", "    ██", "   ██ ", "  ██  ", "  ██  "],
    ["██████", "██  ██", "██████", "██  ██", "██████"],
    ["██████", "██  ██", "██████", "    ██", "██████"],
];

pub fn console() -> Result<()> {
    unsafe {
        if GetConsoleWindow().is_null() || !io::stdout().is_terminal() {
            let _ = FreeConsole();
            if AllocConsole() == 0 {
                anyhow::bail!("Windows could not open the agent terminal");
            }
        }
        let title = "Kintler · Agent\0".encode_utf16().collect::<Vec<_>>();
        SetConsoleTitleW(title.as_ptr());
        SetConsoleCP(65001);
        SetConsoleOutputCP(65001);
    }
    Ok(())
}

fn code_art(code: &str) -> String {
    if code.len() != 6 || !code.bytes().all(|value| value.is_ascii_digit()) {
        return code.to_string();
    }
    let mut result = Vec::new();
    for (row, _) in DIGITS[0].iter().enumerate() {
        let line = code
            .bytes()
            .map(|value| DIGITS[(value - b'0') as usize][row])
            .collect::<Vec<_>>()
            .join("  ");
        result.push(line.clone());
        result.push(line);
    }
    result.join("\n")
}

struct View {
    name: String,
    status: String,
    code: Option<String>,
    online: bool,
    logs: VecDeque<String>,
    scroll: u16,
}

impl View {
    fn event(&mut self, text: String) {
        if let Some(action) = text
            .strip_prefix("Оператор ·")
            .or_else(|| text.strip_prefix("AI ·"))
        {
            self.action(action.trim_start().to_string());
            return;
        }
        if text.starts_with("Ожидает")
            && let Some((_, code)) = text.split_once("Код: ")
        {
            self.code = Some(code.trim().to_string());
            self.online = false;
            self.status = "Ожидаем разрешение · в Controller: /approve и код ниже".into();
            return;
        }
        if text.starts_with("Online") {
            self.code = None;
            self.online = true;
            self.logs.clear();
            self.status = "Подключено · доступ разрешён".into();
            return;
        } else if text.starts_with("Подключение отклонено")
            || text.starts_with("Соединение закрыто")
            || text.starts_with("Связь разорвана")
            || text.starts_with("Нет связи:")
            || text.starts_with("Соединение заменено")
        {
            self.code = None;
            self.online = false;
            self.status =
                "Нет доступа · r — получить новый код; затем /approve в Controller".into();
            return;
        }
        if !self.online {
            self.status = "Подключение к Kintler… · код появится автоматически".into();
            return;
        }
        self.action(text);
    }

    fn action(&mut self, text: String) {
        if self.logs.len() >= 240 {
            self.logs.pop_front();
        }
        self.logs.push_back(text);
        self.scroll = 0;
    }
}

fn draw(frame: &mut Frame, view: &View) {
    let area = frame.area();
    frame.render_widget(Block::default().style(Style::default().bg(BG).fg(FG)), area);
    let parts = Layout::vertical([
        Constraint::Length(4),
        Constraint::Min(1),
        Constraint::Length(3),
    ])
    .margin(2)
    .split(area);
    let header = Text::from(vec![
        Line::from(vec![
            Span::styled(
                "  ╱  KINTLER",
                Style::default().fg(GREEN).add_modifier(Modifier::BOLD),
            ),
            Span::styled(" / AGENT", Style::default().fg(MUTED)),
        ]),
        Line::from(Span::styled(
            format!("     {}", view.name),
            Style::default().fg(MUTED),
        )),
        Line::from(Span::styled(
            format!("     {}", view.status),
            Style::default().fg(FG),
        )),
    ]);
    frame.render_widget(Paragraph::new(header), parts[0]);
    if let Some(code) = &view.code {
        let height = if parts[1].height >= 16 {
            14
        } else {
            parts[1].height
        };
        let centered = Layout::vertical([
            Constraint::Fill(1),
            Constraint::Length(height),
            Constraint::Fill(1),
        ])
        .split(parts[1]);
        let value = if parts[1].width >= 50 && parts[1].height >= 14 {
            format!(
                "{}\n\n{}\n\nКод: {} · введите в Controller · каждое соединение отдельно",
                "КОД ДОСТУПА",
                code_art(code),
                code
            )
        } else {
            format!("КОД ДОСТУПА\n\n{code}\n\nВведите код в Controller")
        };
        frame.render_widget(
            Paragraph::new(value)
                .alignment(Alignment::Center)
                .style(Style::default().fg(GREEN)),
            centered[1],
        );
    } else {
        let lines = view
            .logs
            .iter()
            .rev()
            .take(parts[1].height as usize + view.scroll as usize)
            .skip(view.scroll as usize)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .map(|line| {
                Line::from(vec![
                    Span::styled("  ", Style::default().fg(GREEN)),
                    Span::raw(line.as_str()),
                ])
            })
            .collect::<Vec<_>>();
        frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), parts[1]);
    }
    frame.render_widget(
        Paragraph::new(if view.online {
            "↑ ↓ прокрутка    r — получить новый код    q — выход"
        } else {
            "В Controller: /approve → введите код из этого окна.\nr — получить новый код    q — выход"
        })
            .style(Style::default().fg(MUTED)),
        parts[2],
    );
}

fn launch(
    config: Arc<Config>,
) -> (
    CancellationToken,
    mpsc::Receiver<String>,
    std::thread::JoinHandle<()>,
) {
    let (sender, receiver) = mpsc::channel();
    let stop = CancellationToken::new();
    let token = stop.clone();
    let handle = std::thread::spawn(move || {
        if let Ok(runtime) = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
        {
            runtime.block_on(network::run(config, sender, token));
        }
    });
    (stop, receiver, handle)
}

pub fn run(config: Arc<Config>, title: Option<&str>) -> Result<()> {
    console()?;
    if let Some(title) = title {
        let title = format!("{title}\0").encode_utf16().collect::<Vec<_>>();
        unsafe {
            SetConsoleTitleW(title.as_ptr());
        }
    }
    enable_raw_mode()?;
    execute!(io::stdout(), EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    let mut view = View {
        name: format!(
            "{} · {}",
            config.name,
            if crate::tools::admin() {
                "Admin"
            } else {
                "User"
            }
        ),
        status: "Подключаемся · до ввода кода доступа нет".into(),
        code: None,
        online: false,
        logs: VecDeque::new(),
        scroll: 0,
    };
    let (mut stop, mut receiver, handle) = launch(config.clone());
    let mut handle = Some(handle);
    let result = (|| -> Result<()> {
        let mut dirty = true;
        loop {
            while let Ok(text) = receiver.try_recv() {
                view.event(text);
                dirty = true;
            }
            if dirty {
                terminal.draw(|frame| draw(frame, &view))?;
                dirty = false;
            }
            if event::poll(Duration::from_millis(100))? {
                match event::read()? {
                    Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                        KeyCode::Char('q') | KeyCode::Esc => break,
                        KeyCode::Up => {
                            view.scroll = view.scroll.saturating_add(1).min(240);
                            dirty = true;
                        }
                        KeyCode::Down => {
                            view.scroll = view.scroll.saturating_sub(1);
                            dirty = true;
                        }
                        KeyCode::Char('r') => {
                            stop.cancel();
                            if let Some(previous) = handle.take() {
                                let _ = previous.join();
                            }
                            let (next_stop, next_receiver, next_handle) = launch(config.clone());
                            stop = next_stop;
                            receiver = next_receiver;
                            handle = Some(next_handle);
                            view.code = None;
                            view.online = false;
                            view.status = "Получаем новый код · затем /approve в Controller".into();
                            dirty = true;
                        }
                        _ => {}
                    },
                    Event::Resize(_, _) => dirty = true,
                    _ => {}
                }
            }
        }
        Ok(())
    })();
    stop.cancel();
    if let Some(handle) = handle {
        let _ = handle.join();
    }
    let _ = disable_raw_mode();
    let _ = execute!(terminal.backend_mut(), LeaveAlternateScreen);
    let _ = terminal.show_cursor();
    result
}

pub fn snapshot() -> Result<String> {
    let mut terminal = Terminal::new(TestBackend::new(100, 36))?;
    let view = View {
        name: "Demo device".into(),
        status: "Доступ закрыт".into(),
        code: Some("123456".into()),
        online: false,
        logs: VecDeque::new(),
        scroll: 0,
    };
    terminal.draw(|frame| draw(frame, &view))?;
    let buffer = terminal.backend().buffer();
    let mut result = String::new();
    for row in 0..36 {
        for column in 0..100 {
            result.push_str(buffer[(column, row)].symbol());
        }
        result.push('\n');
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn giant_code_and_bounded_log() {
        assert_eq!(code_art("123456").lines().count(), 10);
        let mut view = View {
            name: "test".into(),
            status: String::new(),
            code: None,
            online: false,
            logs: VecDeque::new(),
            scroll: 0,
        };
        view.event("Ожидает подтверждения. Код: 123456".into());
        assert_eq!(view.code.as_deref(), Some("123456"));
        view.event("Нет связи: Server closed before approval".into());
        assert!(view.code.is_none());
        view.event("Ожидает подтверждения. Код: 654321".into());
        view.event("Online — test".into());
        assert!(view.code.is_none());
        for _ in 0..300 {
            view.event("Оператор · sysinfo".into());
        }
        assert_eq!(view.logs.len(), 240);
        assert_eq!(view.logs.back().unwrap(), "sysinfo");
        view.event("AI · shell echo Код: 123456; echo закрыто".into());
        assert!(view.code.is_none() && view.online);
        assert_eq!(
            view.logs.back().unwrap(),
            "shell echo Код: 123456; echo закрыто"
        );
    }
}
