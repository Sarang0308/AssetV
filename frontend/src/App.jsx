// The whole page: a sidebar with your key numbers on the left, the chat on the right.
import Sidebar from "./components/Sidebar.jsx";
import Chat from "./components/Chat.jsx";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <Chat />
    </div>
  );
}
