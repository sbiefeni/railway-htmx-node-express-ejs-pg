const express = require("express");
const path = require("path");
const { pool, migrate } = require("./database");
const webhookRouter = require("./routes/webhook");

const app = express();

app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "..", "views"));

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// --- Webhook ---
app.use("/webhook", webhookRouter);

// --- Routes ---

app.get("/", async (req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT id, title, completed, created_at FROM todos ORDER BY created_at DESC"
    );
    res.render("home", { todos: rows });
  } catch (err) {
    console.error("Failed to list todos:", err);
    res.status(500).send("Internal Server Error");
  }
});

app.post("/todos", async (req, res) => {
  const { title } = req.body;
  if (!title) return res.status(400).send("title is required");

  try {
    const { rows } = await pool.query(
      "INSERT INTO todos (title) VALUES ($1) RETURNING id, title, completed, created_at",
      [title]
    );
    res.status(201).render("partials/todo-item", { todo: rows[0] });
  } catch (err) {
    console.error("Failed to create todo:", err);
    res.status(500).send("Internal Server Error");
  }
});

app.patch("/todos/:id/toggle", async (req, res) => {
  const { id } = req.params;
  try {
    const { rows } = await pool.query(
      "UPDATE todos SET completed = NOT completed WHERE id = $1 RETURNING id, title, completed, created_at",
      [id]
    );
    if (rows.length === 0) return res.status(404).send("Not found");
    res.render("partials/todo-item", { todo: rows[0] });
  } catch (err) {
    console.error("Failed to toggle todo:", err);
    res.status(500).send("Internal Server Error");
  }
});

app.delete("/todos/:id", async (req, res) => {
  const { id } = req.params;
  try {
    await pool.query("DELETE FROM todos WHERE id = $1", [id]);
    res.send("");
  } catch (err) {
    console.error("Failed to delete todo:", err);
    res.status(500).send("Internal Server Error");
  }
});

app.get("/health", async (req, res) => {
  try {
    await pool.query("SELECT 1");
    res.json({ status: "healthy" });
  } catch (err) {
    res.status(503).json({ status: "unhealthy", error: err.message });
  }
});

// --- Start ---

const PORT = process.env.PORT || 8080;

async function start() {
  try {
    await migrate();
    console.log("Database migrated successfully");
  } catch (err) {
    console.error("Failed to run migrations:", err);
    process.exit(1);
  }

  app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
  });
}

start();
