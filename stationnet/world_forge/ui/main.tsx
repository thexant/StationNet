import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import './style.css';
import './connections.css';
class ErrorBoundary extends React.Component<{children:React.ReactNode},{error:string}>{state={error:''};static getDerivedStateFromError(error:Error){return {error:error.message};}render(){return this.state.error?<div className="fatal"><h1>The editor encountered an error.</h1><p>{this.state.error}</p><p>Your draft is saved on the station; reloading picks up where you left off.</p><button onClick={()=>location.reload()}>Reload World Forge</button></div>:this.props.children;}}
const mount=document.getElementById('world-forge');
if(mount){mount.replaceChildren();createRoot(mount).render(<ErrorBoundary><App/></ErrorBoundary>);}
